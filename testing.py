
hex_path = "hex_files/main.hex"
try:
    num_lines = int(input("Enter number of lines to process: "))
except ValueError:
    print("Please enter a valid number.")
    exit(1)

with open(hex_path, "r") as file:
    for i, line in enumerate(file):
        if i >= num_lines:
            break
        line = line.strip()
        if line.startswith(':'):
            line = line[1:]
        line_bytes = [line[j:j+2] for j in range(0, len(line), 2)]
        print(line_bytes)