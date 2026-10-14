from aiogram.fsm.state import State, StatesGroup


class FaydaBotStates(StatesGroup):
    # Color / template selection
    choosing_color    = State()
    choosing_template = State()

    # Single-ID flow
    waiting_fan       = State()   # user enters FAN number
    waiting_fin       = State()   # user enters FIN number
    waiting_otp       = State()   # user enters OTP received on phone

    # Batch flow
    batch_collecting  = State()   # collecting IDs / waiting for next FAN
    batch_waiting_fin = State()   # user enters FIN for current batch item
    batch_waiting_otp = State()   # user enters OTP for current batch item


class DepositStates(StatesGroup):
    choosing_method   = State()   # user picks CBE or Telebirr
    waiting_amount    = State()   # user enters the amount they sent
    waiting_receipt   = State()   # user uploads screenshot / receipt photo


class AdminStates(StatesGroup):
    waiting_cbe_name        = State()
    waiting_cbe_number      = State()
    waiting_telebirr_name   = State()
    waiting_telebirr_number = State()
    waiting_price           = State()
    waiting_add_admin_id    = State()
    waiting_add_agent_id    = State()   # super-admin: add agent (VIP-only, no earnings)
    waiting_add_vip_id      = State()   # super-admin or agent: add free VIP user
    waiting_set_templates   = State()   # super-admin: set allowed templates for an admin
    waiting_set_user_price  = State()   # admin: set a per-user price override