words = {}

with open("data.csv", "r") as f:
    lines = f.readlines()
    for line in lines:
        line_words = line.split()
        # print(line_words[0])
        if line_words[0] not in words.keys():
            words[line_words[0]] = 1
        else:
            words[line_words[0]] += 1

        if line_words[0] == "الرادود":
            print(line_words[-1])


print(words)

