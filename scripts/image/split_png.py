from PIL import Image

# טעינת תמונת ה-PNG שלך
img = Image.open("images_mi2\processed\images_mi2__en__rooms__images__1_part1__layer0.png")
width, height = img.size

# הגדרת מיקומי החיתוך (שמאל, למעלה, ימין, למטה)
# חתיכה 1: מפיקסל 0 עד 1024
chunk1_box = (0, 0, 1024, height)
chunk1 = img.crop(chunk1_box)
chunk1.save("images_mi2\processed\chunk_0_0.png")

# חתיכה 2: מתחילה ב-1020 (חפיפה של 4 פיקסלים) ומסתיימת ב-2044
chunk2_box = (1020, 0, 2044, height)
chunk2 = img.crop(chunk2_box)
chunk2.save("images_mi2\processed\chunk_0_1.png")

print("התמונה פוצלה בהצלחה לשני חלקים עם 4 פיקסלים של חפיפה!")