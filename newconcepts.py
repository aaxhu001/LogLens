## DATETIME

from datetime import datetime , timedelta

start = datetime(2026, 11 ,17 ,11 , 44 , 00)
start = start + timedelta(seconds = 5)
print(start.strftime("%Y-%m-%dT%H:%M:%SZ"))
print(start)


##RANDOM CHOICESS

import random

fruit = random.choice(["apple","banana","mango"])
# It will give us a random choice from the whole list.
print(fruit)

levels = random.choices(["INFO", "WARN", "ERROR"], weights = [80,15,5])[0]
# RANDOM CHOICES allow us to put weights on the choice , it will choose INFO 80% of the time , and warn 5% of the time
#[0] will give a string as an output not a list.
print(levels)