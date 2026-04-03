import re
import pandas as pd

# Sample input texts (you can replace this with reading from a file)
# texts = [
#     '''اوبريت : [ الممتحنة 3 ] \nبمشاركة الرواديد :\n#علي_حمادي\n#مهدي_سهوان\n#سيد_مصطفى_الموسوي\n#يوسف_الرومي \n#سيد_هاني_الوداعي\n#عبد_الأمير_البلادي \n#عبد_الجبار_الدرازي \n\n\nاستشهاد فاطمة الزهراء (ع)  - 1431 هـ \nماتم سار الكبير''',
#     '''شريط : [ أغثنا يا أبا العلم ]  اغثنا يا ابا العلم\n الرادود : #الشيخ_حسين_الأكرف\n\nاستشهاد الأمام الصادق (ع) 1431 هـ\nموكب ماتم بن سلوم - المنامة''',
#     # Add more texts as needed
# ]

with open("data.csv", "r") as f:
    lines = f.readlines()
    texts = []
    for line in lines:
        texts.append(line.split(",")[0])


# Define regex patterns
album_pattern = r'\[(.*?)\]'  # Matches text within brackets []
artist_pattern = r'#\S+'           # Matches words starting with #
martyrdom_pattern = r'(?:ذكرى استشهاد|ذكرى وفاة|استشهاد|وفاة)\s.*?(?=\n|$)'  # Matches martyrdom lines
date_pattern = r'(\d+\s*هـ)'       # Matches Hijri dates
notes_pattern = r'(?:ماتم|موكب|مسجد|حسينية).+'  # Matches lines starting with specific words

# Initialize a list to store the extracted data
data = []

for text in texts:
    entry = {}

    # Extract Album Name
    album_name_match = re.search(album_pattern, text)
    entry['Album Name'] = album_name_match.group(1).strip() if album_name_match else ''
    # Extract Artists
    artists = re.findall(artist_pattern, text)
    entry['Artists'] = ', '.join([artist.strip('#') for artist in artists]) if artists else ''

    # Extract Martyrdom Anniversary
    martyrdom_match = re.search(martyrdom_pattern, text)
    entry['Martyrdom Anniversary'] = martyrdom_match.group(0).strip() if martyrdom_match else ''

    # Extract Islamic Date
    date_match = re.search(date_pattern, text)
    entry['Islamic Date'] = date_match.group(1).strip() if date_match else ''

    # Extract Notes
    notes_match = re.search(notes_pattern, text)
    entry['Notes'] = notes_match.group(0).strip() if notes_match else ''

    # Append the entry to the data list
    data.append(entry)

# Create a DataFrame and export to CSV
df = pd.DataFrame(data)
df.to_csv('albums.csv', index=False, encoding='utf-8-sig')

# Print the DataFrame
print(df)
