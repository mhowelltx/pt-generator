import logging
import os

from anthropic import Anthropic
from dotenv import load_dotenv

from app.formatter import print_plan
from app.generation import PlanGenerator

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv()

    inputs = {
        "client_name": "Sample Client",
        "session_date": "2026-03-01",
        "session_number": 1,
        "duration_minutes": 50,
        "focus": "Full-body strength with balance + core integration (coach log format).",
        "constraints": [
            "shoulder sensitivity (avoid overhead/front pressing)",
            "knee discomfort (control depth/tempo)",
        ],
        "equipment_available": ["dumbbells 5–15", "bands", "stability ball", "step/box", "cable machine", "selectorized machines"],
        "machine_inventory": ["Leg Press (Seat 6)", "Chest Press (Seat 3)", "Lat Pulldown (Seat 4)", "Hamstring Curl (Seat 4)"],
        "preferences": ["include tempo prescriptions", "include rest times", "include cues and regressions", "include seat/load fields"],
    }

    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    generator = PlanGenerator(client)
    plan = generator.generate(inputs)
    print_plan(plan)
