"""Barra de estado de Claude Code que ademas guarda el USO RESTANTE para el chat.
Claude Code llama a este script (settings.json -> statusLine) y le pasa un JSON por stdin.
Si el JSON trae 'rate_limits' (five_hour / seven_day con used_percentage y resets_at), lo guarda en
claude_usage.json junto al servidor del chat. Imprime una linea corta para la propia barra.
Solo libreria estandar; nunca falla (la barra no debe romperse)."""
import json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("CHAT_CLAUDE_USAGE", os.path.join(os.environ.get("CHAT_DATA", HERE), "claude_usage.json"))
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
line = []
try:
    rl = d.get("rate_limits") or {}
    five, seven = rl.get("five_hour") or {}, rl.get("seven_day") or {}
    if five.get("used_percentage") is not None:
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"five_hour": five, "seven_day": seven, "ts": time.time()}, f)
        os.replace(tmp, OUT)
        line.append(f"5h {100 - float(five['used_percentage']):.0f}% libre")
        if seven.get("used_percentage") is not None:
            line.append(f"7d {100 - float(seven['used_percentage']):.0f}% libre")
    # depuracion: que claves trae esta version (sin datos personales)
    with open(os.path.join(HERE, "claude_statusline_keys.json"), "w", encoding="utf-8") as f:
        json.dump({"keys": sorted(d.keys()), "rate_limits_keys": sorted(rl.keys()) if isinstance(rl, dict) else None, "ts": time.time()}, f)
except Exception:
    pass
model = ((d.get("model") or {}).get("display_name")) if isinstance(d.get("model"), dict) else ""
print(" · ".join([x for x in [model] + line if x]) or "Claude Code")
