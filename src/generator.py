levels = ["INFO","WARN","ERROR"]
services = ["payments","auth","orders"]
messages = {
    "INFO" : ["user logged in", "order created", "cache refreshed"],
    "WARN" : ["high memory usage", "slow databse query", "retrying request"],
    "ERROR" : ["timeout calling db after 3000ms", "payment failed", "connection refused"]
}


from datetime import datetime, timedelta

t1 = datetime(2026, 3, 1, 10,15,32 )
# print(t1)
w1 = t1.strftime("%Y-%m-%dT %H:%M:%SZ")

import random

l1 = random.choice(["INFO","WARN","ERROR"])
l2 = random.choice(["payments","auth","orders"])

# print(l1)
# print(l2)

with open("data/sample.log","w") as f:
    for i in range(0,11):
        t1 = t1 + timedelta(seconds=random.randint(5,60))
        timestamp = t1.strftime("%Y-%m-%dT%H:%M:%SZ")

        level = random.choice(levels)
        service = random.choice(services)
        message = random.choice(messages[level])


        
        f.write(f"{timestamp} {levels} {messages}\n")
        










