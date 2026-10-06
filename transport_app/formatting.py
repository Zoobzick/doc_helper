import re


def format_site(value):
    value = value.strip()
    value = re.sub(r"^участок\s*(?=№|\d|\s|$)", "", value, flags=re.IGNORECASE).strip()
    value = value.lstrip("№").strip()
    return "Участок №" + value if value else ""


def place_numbers(value):
    """Keep the entered order and identifiers, accepting legacy place prefixes."""
    numbers = []
    for part in value.split(","):
        part = re.sub(r"^(?:площадки|площадка|пл\.?)\s*(?=№|\d|\s|$)", "", part.strip(), flags=re.IGNORECASE)
        part = part.strip().lstrip("№").strip()
        if part:
            numbers.append(part)
    return numbers


def format_place(value):
    numbers = place_numbers(value)
    if not numbers:
        return ""
    label = "Площадка" if len(numbers) == 1 else "Площадки"
    return label + " " + ", ".join("№" + number for number in numbers)
