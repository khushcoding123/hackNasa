"""Copy the supplied Cassini glTF into web/assets with material colours filled in.

The supplied file has every material's base colour set to black (the Blender export lost the colours), which renders as
a silhouette against space. The material names say what each one is, so each gets a plausible colour. Geometry and
the binary buffer are copied byte for byte; only the JSON material block changes.

    python scripts/colour_cassini_glb.py "C:/Users/you/Downloads/Cassini Assembly.glb"
"""
import argparse
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# name: (base colour, metallic, roughness). Cassini as flown: white high-gain dish, gold thermal foil on the bus,
# bare and anodised aluminium on the booms and frames, black antennas and orange-red markings.
COLOURS = {
    "white": ([0.93, 0.93, 0.91, 1], 0.0, 0.5),          # high-gain dish and white panels
    "gold": ([0.86, 0.60, 0.14, 1], 0.95, 0.28),         # gold Kapton / MLI foil
    "aluminum": ([0.78, 0.79, 0.82, 1], 0.85, 0.35),     # bare aluminium
    "anod_aluminum": ([0.50, 0.52, 0.56, 1], 0.75, 0.4), # anodised, darker
    "silver": ([0.88, 0.89, 0.91, 1], 0.9, 0.25),
    "grey": ([0.46, 0.47, 0.50, 1], 0.4, 0.5),
    "black": ([0.03, 0.03, 0.035, 1], 0.1, 0.55),        # antennas, instrument apertures
    "red_orange": ([0.88, 0.30, 0.07, 1], 0.1, 0.45),    # RTG / marking colour
}
EMISSIVE = 0.14  # a little self-light so the side facing away from the Sun is still readable


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "web" / "assets" / "cassini-satellite.glb")
    args = parser.parse_args()
    data = args.source.read_bytes()
    magic, version, _ = struct.unpack("<4sII", data[:12])
    assert magic == b"glTF" and version == 2
    chunks, offset = [], 12
    while offset < len(data):
        length, kind = struct.unpack("<I4s", data[offset:offset + 8])
        chunks.append([kind, data[offset + 8:offset + 8 + length]])
        offset += 8 + length
    gltf = json.loads(chunks[0][1])
    for material in gltf["materials"]:
        colour, metallic, roughness = COLOURS[material["name"]]
        material["pbrMetallicRoughness"] = {"baseColorFactor": colour, "metallicFactor": metallic, "roughnessFactor": roughness}
        material["emissiveFactor"] = [round(c * EMISSIVE, 4) for c in colour[:3]]
        material.get("extensions", {}).pop("KHR_materials_specular", None)  # specularFactor 0 would remove every highlight
        if not material.get("extensions"):
            material.pop("extensions", None)
    gltf["extensionsUsed"] = [e for e in gltf.get("extensionsUsed", []) if e != "KHR_materials_specular"]
    text = json.dumps(gltf, separators=(",", ":")).encode()
    chunks[0][1] = text + b" " * (-len(text) % 4)
    body = b"".join(struct.pack("<I4s", len(payload), kind) + payload for kind, payload in chunks)
    args.out.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
