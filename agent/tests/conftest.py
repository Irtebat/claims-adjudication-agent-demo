import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
# services/src: the supplier-recovery derivation tests prove the downstream outcome by
# running the real consumer predicate (consumer_core.plan_action) over the real event
# payload (events.build_adjudicated_payload), so both must be importable here.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "src"))
