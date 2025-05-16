#!/usr/bin/env python3
import sys, os, asyncio

# 1️⃣  add repo root (one level up from /cli) to PYTHONPATH **first**
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

# 2️⃣  now we can safely import from app
from app.orchestrator import orchestrate

# 3️⃣  rest of the CLI
prompt = " ".join(sys.argv[1:]) or input("prompt › ")
print(asyncio.run(orchestrate(prompt)))
