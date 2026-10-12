import asyncio
import io
import math
import traceback
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageOps
from aiogram import Bot
from aiogram.types import BufferedInputFile

from core.api.fayda_client import FaydaClient, FaydaSession, FaydaConnectionError
from core.image.image_generator import generate_final_id_image
from core.image.image_generator_b import generate_final_id_image_b
from core.image.qr_service import generate_code128_barcode, generate_fayda_qr
from app.config import settings
from utils.texts import INSUFFICIENT_BALANCE, BATCH_NO_BALANCE, BATCH_PARTIAL_BALANCE


class ProcessingService:
    def __init__(self, bot: Bot, billing=None):
        self.bot = bot
        self.billing = billing  # BillingService; may be None in tests
        self.lock = asyncio.Lock()  # prevent parallel heavy CPU processing
        self._fayda = FaydaClient()

    # ──────────────────────────────────────────────────────────────────────────
    # OTP FLOW (called from bot handlers)
    # ──────────────────────────────────────────────────────────────────────────

    async def initiate_otp(self, fan: str) -> FaydaSession:
        """
        Steps 1-4: seed cookies, get CSRF, get oauth-details, send OTP.
        Returns the FaydaSession so the handler can store it in FSM state.
        """
        session = await self._fayda.initiate(fan)
        await self._fayda.send_otp(session)
        return session

    async def verify_otp_only(self, session: FaydaSession, otp: str) -> dict:
        """Verify OTP and return auth code data."""
        return await self._fayda.verify_otp(session, otp)

    async def verify_and_generate(
        self,
        session: FaydaSession,
        otp: str,
        fin: str,
        fan: str = "",
        chat_id: int = 0,
        color: bool = True,
        template: str = "A",
        status_message_id: Optional[int] = None,
        user_id: int = 0,
    ) -> bool:
        """
        Verify OTP -> fetch profile -> generate QR & barcode -> generate ID card -> send to chat.

        Debits the user's balance (via billing) ONLY after the card is delivered,
        so Fayda/OTP/generation failures never cost the user anything.
        """
        status_msg_id = status_message_id
        price = (
            await self.billing.get_price_for_user(user_id)
            if self.billing and user_id
            else settings.PRICE_PER_DOWNLOAD
        )
        try:
            # 0. Billing gate — block before any Fayda work if balance can't cover a card.
            if self.billing and user_id and price > 0:
                bal = await self.billing.get_balance(user_id)
                if bal < price:
                    await self._edit_or_send(
                        chat_id, status_msg_id,
                        INSUFFICIENT_BALANCE.format(balance=bal, price=price),
                    )
                    return False

            # 1. Update status
            if status_msg_id:
                await self._edit_or_send(chat_id, status_msg_id, "🔐 Verifying OTP...")
            else:
                msg = await self.bot.send_message(chat_id, "🔐 Verifying OTP...")
                status_msg_id = msg.message_id

            # 2. Verify OTP + get auth-code
            try:
                await self._fayda.verify_otp(session, otp)
            except ValueError as e:
                await self._edit_or_send(chat_id, status_msg_id, f"❌ {e}")
                return False

            await self._edit_or_send(chat_id, status_msg_id, "🌐 Fetching your Fayda profile...")

            # 3. Fetch resident profile
            profile = await self._fayda.fetch_profile(session)
            fan_val = fan or session.fan
            text_data = FaydaClient.profile_to_text_data(profile, fan=fan_val, fin=fin)
            photo_bytes = FaydaClient.extract_photo_bytes(profile)

            # Convert photo bytes -> numpy array for image generator
            photo_np = None
            if photo_bytes:
                photo_np = cv2.imdecode(
                    np.frombuffer(photo_bytes, np.uint8), cv2.IMREAD_COLOR
                )

            # 4. Generate QR code using FAN and profile data
            await self._edit_or_send(chat_id, status_msg_id, "🔄 Generating QR Code & Card...")
            qr_img = None
            try:
                dob_val = text_data.get("date_of_birth_greg") or "2000/01/01"
                # The QR's A: field holds the 16-digit FAN (card number), matching
                # the real Fayda QR payload — not the 12-digit FIN.
                qr_fan = fan_val or text_data.get("fan_code", "")
                qr_img = await asyncio.to_thread(
                    generate_fayda_qr,
                    fan=qr_fan,
                    name=text_data.get("name_en", "Resident"),
                    dob=dob_val,
                    gender=text_data.get("sex_en", "M"),
                    photo_b64=profile.get("photo"),
                )
            except Exception as e:
                print(f"[Warning] Failed to generate QR code: {e}")

            # 5. Generate Barcode using FAN or FIN
            barcode_code = fan_val or fin
            barcode_img = await asyncio.to_thread(generate_code128_barcode, barcode_code)

            # 6. Generate ID card (CPU-heavy, locked)
            async with self.lock:
                generator_func = generate_final_id_image_b if template == "B" else generate_final_id_image
                image_bytes = await asyncio.to_thread(
                    generator_func,
                    text_data=text_data,
                    photo=photo_np,
                    qrcode_img=qr_img,
                    barcode_img=barcode_img,
                    font_amharic="./fonts/truetype/abyssinica/AbyssinicaSIL-Regular.ttf",
                    font_english="./fonts/truetype/noto/NotoSans-Regular.ttf",
                    font_size=17,
                    boldness=0.5,
                    color=color,
                )

            # 7. Send result.
            #    send_document (not send_photo): Telegram re-encodes photos to
            #    lossy JPEG and caps them near 1280px, which would throw away the
            #    3840x1170 render. A document is delivered byte-for-byte.
            await self.bot.send_document(
                chat_id=chat_id,
                document=BufferedInputFile(image_bytes, filename=f"fayda_id_{template}.png"),
                caption=f"✅ Your ID Card is ready! ({'Color' if color else 'B&W'}, Template {template})",
            )

            # Debit only now that the card is actually delivered.
            if self.billing and user_id:
                await self.billing.charge(user_id, price, f"single card (template {template})")

            await self._delete_safe(chat_id, status_msg_id)
            return True

        except FaydaConnectionError as e:
            print(f"[ProcessingService] Fayda connection error: {e}")
            await self._edit_or_send(
                chat_id, status_msg_id,
                "⚠️ Fayda's servers aren't responding right now. This is usually "
                "temporary — please wait a minute and try again.",
            )
            return False
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[ProcessingService] Error: {e}\n{tb}")
            await self._edit_or_send(
                chat_id, status_msg_id,
                f"❌ Error generating ID: {e}",
            )
            return False
        finally:
            await session.close()

    # ──────────────────────────────────────────────────────────────────────────
    # BATCH FLOW
    # ──────────────────────────────────────────────────────────────────────────

    async def verify_and_generate_batch(
        self,
        entries: list[dict],   # [{"session": FaydaSession, "fan": str, "fin": str, "verified": bool, "otp": Optional[str]}, ...]
        chat_id: int,
        color: bool = True,
        template: str = "A",
        status_message_id: Optional[int] = None,
        user_id: int = 0,
    ) -> bool:
        """
        Process a batch of entries, generate ID cards, and tile onto A4 sheets.

        The user is debited per card only after that card's A4 page is delivered,
        so partial failures never overcharge. If the balance can't cover every
        requested card, the batch is trimmed to what the user can afford (with a
        warning).
        """
        status_msg_id = status_message_id
        price = (
            await self.billing.get_price_for_user(user_id)
            if self.billing and user_id
            else settings.PRICE_PER_DOWNLOAD
        )
        A4_WIDTH, A4_HEIGHT = 905, 1280
        ID_W, ID_H, GAP = 388, 244, 28
        ROW_W = ID_W * 2 + GAP

        all_rows: list[Image.Image] = []

        # Billing gate — trim the batch to what the balance covers before any work.
        if self.billing and user_id and price > 0:
            bal = await self.billing.get_balance(user_id)
            affordable = bal // price
            if affordable <= 0:
                await self._edit_or_send(
                    chat_id, status_msg_id,
                    BATCH_NO_BALANCE.format(balance=bal, price=price),
                )
                for entry in entries:
                    try:
                        await entry["session"].close()
                    except Exception:
                        pass
                return False
            if len(entries) > affordable:
                await self.bot.send_message(
                    chat_id,
                    BATCH_PARTIAL_BALANCE.format(
                        affordable=affordable, requested=len(entries),
                        balance=bal, price=price,
                    ),
                )
                # Close the sessions for the entries we're dropping (the loop below
                # only closes the ones it processes).
                for entry in entries[affordable:]:
                    try:
                        await entry["session"].close()
                    except Exception:
                        pass
                entries = entries[:affordable]

        try:
            for i, entry in enumerate(entries):
                session: FaydaSession = entry["session"]
                fan: str = entry.get("fan", session.fan)
                fin: str = entry.get("fin", "")
                verified: bool = entry.get("verified", False)
                otp: Optional[str] = entry.get("otp")

                # If not verified yet, verify now
                if not verified and otp:
                    await self._edit_or_send(
                        chat_id, status_msg_id,
                        f"🔐 Verifying OTP for ID #{i+1} of {len(entries)}..."
                    )
                    try:
                        await self._fayda.verify_otp(session, otp)
                    except ValueError as e:
                        await self.bot.send_message(chat_id, f"⚠️ ID #{i+1} ({fan}) OTP failed: {e} — skipping.")
                        await session.close()
                        continue
                    except FaydaConnectionError:
                        await self.bot.send_message(
                            chat_id,
                            f"⚠️ ID #{i+1} ({fan}) skipped — Fayda's server was unreachable. "
                            f"Please retry this one later."
                        )
                        await session.close()
                        continue

                await self._edit_or_send(
                    chat_id, status_msg_id,
                    f"🌐 Fetching profile #{i+1} of {len(entries)}..."
                )
                try:
                    profile = await self._fayda.fetch_profile(session)
                except Exception as e:
                    await self.bot.send_message(chat_id, f"⚠️ Failed to fetch profile for ID #{i+1}: {e}")
                    await session.close()
                    continue

                await session.close()

                text_data = FaydaClient.profile_to_text_data(profile, fan=fan, fin=fin)
                photo_bytes = FaydaClient.extract_photo_bytes(profile)
                photo_np = None
                if photo_bytes:
                    photo_np = cv2.imdecode(
                        np.frombuffer(photo_bytes, np.uint8), cv2.IMREAD_COLOR
                    )

                # Generate QR code
                qr_img = None
                try:
                    dob_val = text_data.get("date_of_birth_greg") or "2000/01/01"
                    # QR A: field = 16-digit FAN (card number), like the real Fayda QR.
                    qr_fan = fan or text_data.get("fan_code", "")
                    qr_img = await asyncio.to_thread(
                        generate_fayda_qr,
                        fan=qr_fan,
                        name=text_data.get("name_en", "Resident"),
                        dob=dob_val,
                        gender=text_data.get("sex_en", "M"),
                        photo_b64=profile.get("photo"),
                    )
                except Exception as e:
                    print(f"[Warning] Failed to generate QR code in batch: {e}")

                barcode_code = fan or fin
                barcode_img = await asyncio.to_thread(generate_code128_barcode, barcode_code)

                await self._edit_or_send(
                    chat_id, status_msg_id,
                    f"🔄 Generating card #{i+1} of {len(entries)}..."
                )

                async with self.lock:
                    generator_func = generate_final_id_image_b if template == "B" else generate_final_id_image
                    image_bytes = await asyncio.to_thread(
                        generator_func,
                        text_data=text_data,
                        photo=photo_np,
                        qrcode_img=qr_img,
                        barcode_img=barcode_img,
                        font_amharic="./fonts/truetype/abyssinica/AbyssinicaSIL-Regular.ttf",
                        font_english="./fonts/truetype/noto/NotoSans-Regular.ttf",
                        font_size=17,
                        boldness=0.5,
                        color=color,
                    )

                # ── Trim + layout ──────────────────────────────────────────────
                full_img = Image.open(io.BytesIO(image_bytes))
                w, h = full_img.size
                front_raw = full_img.crop((0, 0, w // 2, h))
                back_raw  = full_img.crop((w // 2, 0, w, h))

                def trim_all(im, threshold=10):
                    gray = im.convert("L")
                    inv  = ImageOps.invert(gray)
                    bbox = inv.point(lambda p: p > threshold and 255).getbbox()
                    return im.crop(bbox) if bbox else im

                front = trim_all(front_raw).resize((ID_W, ID_H), Image.Resampling.LANCZOS)
                back  = trim_all(back_raw).resize((ID_W, ID_H), Image.Resampling.LANCZOS)

                row = Image.new("RGB", (ROW_W, ID_H), (255, 255, 255))
                row.paste(front, (0, 0))
                row.paste(back,  (ID_W + GAP, 0))
                all_rows.append(row)

            if not all_rows:
                await self.bot.send_message(chat_id, "❌ No IDs were processed successfully.")
                return False

            # ── Pack into A4 pages (5 rows each) ──────────────────────────────
            num_pages = math.ceil(len(all_rows) / 5)
            for p in range(num_pages):
                await self._edit_or_send(
                    chat_id, status_msg_id,
                    f"📄 Generating A4 page {p+1} of {num_pages}..."
                )
                batch = all_rows[p*5 : p*5+5]
                v_gap = 10
                total_h = len(batch) * ID_H + (len(batch) - 1) * v_gap
                start_y = (A4_HEIGHT - total_h) // 2
                x_pos   = (A4_WIDTH - ROW_W) // 2

                canvas = Image.new("RGB", (A4_WIDTH, A4_HEIGHT), (255, 255, 255))
                for j, row in enumerate(batch):
                    canvas.paste(row, (x_pos, start_y + j * (ID_H + v_gap)))

                # Mirror for double-sided printing
                canvas = canvas.transpose(Image.FLIP_LEFT_RIGHT)

                out = io.BytesIO()
                canvas.save(out, format="PNG")
                out.seek(0)

                await self.bot.send_document(
                    chat_id=chat_id,
                    document=BufferedInputFile(out.read(), filename=f"A4_IDs_PAGE_{p+1}.png"),
                    caption=(
                        f"✅ A4 Page {p+1} ({len(batch)} IDs)\n"
                        f"Layout: [Back | Front] (MIRRORED)\n"
                        f"Type: {'Color' if color else 'B&W'}"
                    ),
                )

                # Debit only after this page is actually delivered — one card's
                # price per ID on the page. If a later page fails, delivered pages
                # stay charged (correct) and undelivered ones are never charged.
                if self.billing and user_id:
                    await self.billing.charge(
                        user_id, price * len(batch),
                        f"batch A4 page {p+1} ({len(batch)} cards, template {template})",
                    )

            await self._delete_safe(chat_id, status_msg_id)
            await self.bot.send_message(chat_id, f"✅ All {len(all_rows)} IDs processed and sent!")
            return True

        except FaydaConnectionError as e:
            print(f"[ProcessingService] Batch Fayda connection error: {e}")
            await self._edit_or_send(
                chat_id, status_msg_id,
                "⚠️ Fayda's servers aren't responding right now. This is usually "
                "temporary — please wait a minute and try again.",
            )
            return False
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[ProcessingService] Batch error: {e}\n{tb}")
            await self._edit_or_send(
                chat_id, status_msg_id,
                f"❌ Batch error: {e}",
            )
            return False

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    async def _edit_or_send(self, chat_id: int, msg_id: Optional[int], text: str) -> int:
        if msg_id:
            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id, message_id=msg_id, text=text
                )
                return msg_id
            except Exception:
                pass
        msg = await self.bot.send_message(chat_id, text)
        return msg.message_id

    async def _delete_safe(self, chat_id: int, msg_id: Optional[int]):
        if msg_id:
            try:
                await self.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:
                pass