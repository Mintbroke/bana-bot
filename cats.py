import enum

class Rarity(enum.Enum):
    BANA_RARE: int = 1
    UBER_RARE: int = 2
    SUPER_RARE: int = 3
    RARE: int = 4

class cat:
    def __init__(self, name, banner, rarity: int, quality, image_url):
        self.name = name
        self.banner = banner
        self.rarity = rarity
        self.quality = quality
        self.image_url = image_url
        self.init_cat()
    
    def init_cat(self):
        if self.rarity == Rarity.BANA_RARE:
            self.name = "Bana Rare Cat"
        if self.rarity == Rarity.UBER_RARE:
            self.name = "Uber Rare Cat"
        if self.rarity == Rarity.SUPER_RARE:
            self.name = "Super Rare Cat"
        if self.rarity == Rarity.RARE:
            self.name = "Rare Cat"
    
