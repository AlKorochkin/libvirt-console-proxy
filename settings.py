import os
from dotenv import load_dotenv

load_dotenv(".")

URI = os.getenv("URI", default="qemu:///system")
UUID = os.getenv("UUID",default="b140b2f6-9923-4aab-b760-c1803f7e497e")