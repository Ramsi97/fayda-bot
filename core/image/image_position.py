
import cv2
import numpy as np


img = cv2.imread("/home/ramsi/Desktop/projects/updated_bot/data/templates/template.png")
h, w = img.shape[:2]


#PHOTO
cv2.rectangle(img, (200, 250), (500, 575), (0, 0, 0), 1)

#FAN
cv2.rectangle(img, (612, 524), (910, 608), (0, 0, 0), 1)



#full name Amharic
cv2.rectangle(img, (565, 215), (800, 240), (0, 0, 0), 1)
# full name english
cv2.rectangle(img, (565, 260), (800, 285), (0, 0, 0), 1)


#date of birth
cv2.rectangle(img, (565, 342), (800, 367), (0, 0, 0), 1)

#sex
cv2.rectangle(img, (565, 407), (800, 432), (0, 0, 0), 1)

#Expire Date
cv2.rectangle(img, (565, 465), (800, 490), (0, 0, 0), 1)

#phone Number
cv2.rectangle(img, (1335, 110), (1560, 135), (0, 0, 0), 1)


#Address
cv2.rectangle(img, (1335, 275), (1600, 500), (0, 0, 0), 1)

#QR Code
cv2.rectangle(img, (1755, 68), (2305, 618), (0, 0, 0), 1)

#FIN
cv2.rectangle(img, (1325, 567), (1630, 610), (0, 0, 0), 1)

#mini photo
cv2.rectangle(img, (1000, 524), (1100, 624), (0, 0, 0), 1)

#date of issue greg
cv2.rectangle(img, (165, 180), (185, 290), (0, 0, 0), 1)
#date of issue ethiopian
cv2.rectangle(img, (165, 410), (185, 520), (0, 0, 0), 1)

cv2.imwrite("/home/ramsi/Desktop/projects/updated_bot/storage/outputs/image.png", img)

