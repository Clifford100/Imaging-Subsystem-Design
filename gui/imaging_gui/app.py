from __future__ import annotations

import base64
import csv
import json
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

# ------------------------------------------------------------
# Imaging Subsystem GUI backend
# ------------------------------------------------------------
# This GUI keeps the same HTML/CSS/JavaScript appearance, but uses the
# proven grayscale-only three-shape detector logic from the tested script:
#   - median/selected background model
#   - difference map + Otsu thresholding
#   - Canny edges + filled contours
#   - top-right LED/reflection artefact exclusion only
#   - circle, rod and star-like irregular classification
#   - CSV outputs and processing-stage images
# ------------------------------------------------------------

APP_ROOT = Path(__file__).resolve().parent
CAPTURED_DIR = APP_ROOT / "captured_images"
BACKGROUND_DIR = APP_ROOT / "background_images"
OUTPUT_DIR = APP_ROOT / "outputs"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

FOV_WIDTH_MM = 85.0
FOV_HEIGHT_MM = 85.0
DEFAULT_DATA_STRING = "DATA:21,1000,5,1,2026,05,12,13,53,22"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

CAPTURED_DIR.mkdir(exist_ok=True)
BACKGROUND_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


@dataclass
class DetectorConfig:
    fov_width_mm: float = FOV_WIDTH_MM
    fov_height_mm: float = FOV_HEIGHT_MM
    min_area_px: float = 45.0
    max_area_px: float = 12000.0
    diff_sigma: float = 3.0
    min_diff_threshold: int = 8
    canny_low: int = 20
    canny_high: int = 60
    edge_dilate_k: int = 3
    close_k: int = 5
    open_k: int = 3
    expected_particles: int = 0
    rod_aspect_min: float = 2.35
    rod_pca_ratio_min: float = 2.45
    min_rod_length_px: float = 22.0
    max_rod_width_px: float = 18.0
    rod_rectangularity_min: float = 0.25
    star_defects_min: int = 3
    star_solidity_max: float = 0.86
    star_radial_cv_min: float = 0.12
    star_min_defect_depth_px: float = 1.5
    star_hull_roughness_min: float = 1.05
    circle_compact_aspect_max: float = 2.20
    circle_compact_pca_max: float = 2.45
    ignore_top_right_corner_px: int = 34
    no_labels: bool = False


CFG = DetectorConfig()


# =============================================================================
# General GUI utilities
# =============================================================================

def allowed_image(path_or_name: str) -> bool:
    return Path(path_or_name).suffix.lower() in ALLOWED_EXTENSIONS


def list_image_folder(folder: Path, url_prefix: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for path in sorted(folder.iterdir()):
        if path.is_file() and allowed_image(path.name):
            stat = path.stat()
            items.append({
                "filename": path.name,
                "size_kb": round(stat.st_size / 1024, 2),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "url": f"/{url_prefix}/{path.name}",
            })
    return items


def list_images() -> List[Dict[str, Any]]:
    return list_image_folder(CAPTURED_DIR, "captured_images")


def list_backgrounds() -> List[Dict[str, Any]]:
    return list_image_folder(BACKGROUND_DIR, "background_images")


def list_image_paths(folder: Path) -> List[Path]:
    return sorted([p for p in folder.iterdir() if p.is_file() and allowed_image(p.name)])


def image_to_base64(img: np.ndarray) -> str:
    ok, buffer = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("Could not encode image stage.")
    return "data:image/png;base64," + base64.b64encode(buffer).decode("utf-8")


def save_stage(run_dir: Path, name: str, img: np.ndarray) -> str:
    output_path = run_dir / f"{name}.png"
    cv2.imwrite(str(output_path), img)
    return image_to_base64(img)


def safe_read_color(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not read image: {path.name}")
    return img


def safe_read_gray(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"Could not read image: {path.name}")
    return img


# =============================================================================
# Metadata handling
# =============================================================================

def parse_data_string(data_string: str) -> Dict[str, Any]:
    raw = (data_string or "").strip()
    if not raw.startswith("DATA:"):
        raw = DEFAULT_DATA_STRING

    values = raw.replace("DATA:", "", 1).split(",")
    if len(values) != 10:
        raw = DEFAULT_DATA_STRING
        values = raw.replace("DATA:", "", 1).split(",")

    try:
        temp_c = float(values[0])
        pressure_hpa = float(values[1])
        conductivity_s_cm = float(values[2])
        activation_flag = int(values[3])
        year, month, day = int(values[4]), int(values[5]), int(values[6])
        hour, minute, second = int(values[7]), int(values[8]), int(values[9])
        timestamp = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
    except Exception:
        return parse_data_string(DEFAULT_DATA_STRING)

    return {
        "raw_data_string": raw,
        "temperature_deg_c": temp_c,
        "pressure_hpa": pressure_hpa,
        "conductivity_s_cm": conductivity_s_cm,
        "activation_flag": activation_flag,
        "event_type": "Post-activation / motor event" if activation_flag == 1 else "Baseline event",
        "timestamp": timestamp,
        "metadata_source": "DATA string",
    }


def metadata_candidates(image_path: Path) -> List[Path]:
    stem = image_path.stem
    parent = image_path.parent
    return [
        parent / f"{stem}.txt",
        parent / f"{stem}.csv",
        parent / f"{stem}.json",
        parent / "metadata.txt",
        parent / "metadata.csv",
        parent / "metadata.json",
    ]


def load_metadata(image_path: Path) -> Dict[str, Any]:
    for candidate in metadata_candidates(image_path):
        if not candidate.exists():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore").strip()
            if candidate.suffix.lower() == ".json":
                data = json.loads(text)
                if "raw_data_string" in data:
                    parsed = parse_data_string(str(data["raw_data_string"]))
                    parsed.update(data)
                    parsed["metadata_source"] = candidate.name
                    return parsed
                data["metadata_source"] = candidate.name
                return data
            match = re.search(r"DATA:[^\n\r]+", text)
            if match:
                parsed = parse_data_string(match.group(0).strip())
                parsed["metadata_source"] = candidate.name
                return parsed
        except Exception:
            continue

    parsed = parse_data_string(DEFAULT_DATA_STRING)
    parsed["metadata_source"] = "Default example DATA string"
    return parsed


# =============================================================================
# Proven detector background selection/model
# =============================================================================

def find_best_background_for_image(image_path: Path) -> Optional[Path]:
    backgrounds = list_image_paths(BACKGROUND_DIR)
    if not backgrounds:
        return None

    image_stem = image_path.stem.lower()
    for bg in backgrounds:
        if bg.stem.lower() == image_stem:
            return bg

    preferred_names = [
        "background.png", "background.jpg", "background.jpeg",
        "reference.png", "reference.jpg", "empty.png", "empty.jpg",
        "viewport_background.png", "viewport_background.jpg",
    ]
    lower_lookup = {bg.name.lower(): bg for bg in backgrounds}
    for name in preferred_names:
        if name in lower_lookup:
            return lower_lookup[name]

    return backgrounds[0]


def build_background_model(background_filename: Optional[str], image_path: Path, target_shape: Tuple[int, int]) -> Tuple[Optional[np.ndarray], str, int]:
    """Build a median background model using the tested-script approach.

    If a background is selected manually, only that image is used.
    If Auto is selected, all images in background_images/ are used to form a
    median model. This is closest to the proven command-line detector.
    """
    files: List[Path] = []

    if background_filename and background_filename != "AUTO":
        safe_name = secure_filename(background_filename)
        candidate = BACKGROUND_DIR / safe_name
        if not candidate.exists() or not allowed_image(candidate.name):
            raise ValueError("Selected background image was not found in background_images/.")
        files = [candidate]
    else:
        all_backgrounds = list_image_paths(BACKGROUND_DIR)
        if all_backgrounds:
            files = all_backgrounds

    if not files:
        return None, "Estimated from selected image because no background image was found", 0

    stack = []
    h, w = target_shape
    for file in files:
        gray = safe_read_gray(file)
        if gray.shape != target_shape:
            gray = cv2.resize(gray, (w, h), interpolation=cv2.INTER_AREA)
        stack.append(gray)

    background = np.median(np.stack(stack, axis=0), axis=0).astype(np.uint8)

    if len(files) == 1:
        source = f"background_images/{files[0].name}"
    else:
        best = find_best_background_for_image(image_path)
        source = f"Median model from {len(files)} background image(s)"
        if best is not None:
            source += f"; preferred match: {best.name}"

    return background, source, len(files)


# =============================================================================
# Segmentation from the proven script
# =============================================================================

def odd_kernel_from_sigma(sigma: float) -> int:
    k = max(3, int(round(4 * sigma + 1)))
    return k if k % 2 == 1 else k + 1


def apply_exclusion_zones(mask: np.ndarray, cfg: DetectorConfig) -> np.ndarray:
    cleaned = mask.copy()
    h, w = cleaned.shape[:2]
    top_right = max(0, int(cfg.ignore_top_right_corner_px))
    if top_right > 0:
        cleaned[0:top_right, max(0, w - top_right):w] = 0
    return cleaned


def component_inside_top_right_exclusion(features: Dict[str, Any], image_shape: Tuple[int, int], cfg: DetectorConfig) -> bool:
    h, w = image_shape[:2]
    cx = float(features["centroid_x_px"])
    cy = float(features["centroid_y_px"])
    top_right = max(0, int(cfg.ignore_top_right_corner_px))
    return bool(top_right > 0 and cx > (w - top_right) and cy < top_right)


def make_difference_map(gray: np.ndarray, background: np.ndarray, sigma: float) -> Tuple[np.ndarray, np.ndarray]:
    if background.shape != gray.shape:
        background = cv2.resize(background, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_AREA)
    diff_raw = cv2.absdiff(gray, background)
    k = odd_kernel_from_sigma(sigma)
    diff_blur = cv2.GaussianBlur(diff_raw, (k, k), sigmaX=sigma, sigmaY=sigma)
    return diff_raw, diff_blur


def threshold_difference(diff_blur: np.ndarray, min_threshold: int) -> Tuple[int, np.ndarray]:
    otsu_threshold, _ = cv2.threshold(diff_blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    threshold_value = max(int(otsu_threshold), int(min_threshold))
    _, region_mask = cv2.threshold(diff_blur, threshold_value, 255, cv2.THRESH_BINARY)
    return threshold_value, region_mask


def fill_edge_contours(edge_mask: np.ndarray) -> np.ndarray:
    contours, _ = cv2.findContours(edge_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(edge_mask)
    if contours:
        cv2.drawContours(filled, contours, -1, 255, thickness=cv2.FILLED)
    return filled


def make_particle_mask(diff_blur: np.ndarray, region_mask: np.ndarray, cfg: DetectorConfig) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    edges = cv2.Canny(diff_blur, cfg.canny_low, cfg.canny_high)

    if cfg.edge_dilate_k > 1:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.edge_dilate_k, cfg.edge_dilate_k))
        edges = cv2.dilate(edges, k, iterations=1)

    filled_edges = fill_edge_contours(edges)
    combined = cv2.bitwise_or(region_mask, filled_edges)
    combined = apply_exclusion_zones(combined, cfg)

    if cfg.close_k > 1:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.close_k, cfg.close_k))
        combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, k, iterations=1)

    if cfg.open_k > 1:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.open_k, cfg.open_k))
        combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, k, iterations=1)

    combined = apply_exclusion_zones(combined, cfg)
    return edges, filled_edges, combined


# =============================================================================
# Shape features from the proven script
# =============================================================================

def pca_features(contour: np.ndarray) -> Tuple[float, float, float]:
    pts = contour.reshape(-1, 2).astype(np.float32)
    if len(pts) < 5:
        return 0.0, 0.0, 1.0
    _, _, eigenvalues = cv2.PCACompute2(pts, mean=None)
    vals = eigenvalues.flatten()
    if len(vals) < 2 or vals[1] <= 1e-6:
        return 0.0, 0.0, 999.0
    major = 4.0 * math.sqrt(float(vals[0]))
    minor = 4.0 * math.sqrt(float(vals[1]))
    ratio = major / minor if minor > 1e-6 else 999.0
    return major, minor, ratio


def convexity_defect_features(contour: np.ndarray) -> Tuple[int, float]:
    defect_count = 0
    max_depth = 0.0
    try:
        hull_indices = cv2.convexHull(contour, returnPoints=False)
        if hull_indices is None or len(hull_indices) < 4 or len(contour) < 4:
            return 0, 0.0
        defects = cv2.convexityDefects(contour, hull_indices)
        if defects is None:
            return 0, 0.0
        for i in range(defects.shape[0]):
            depth_px = float(defects[i, 0, 3]) / 256.0
            max_depth = max(max_depth, depth_px)
            if depth_px >= 1.5:
                defect_count += 1
    except cv2.error:
        return 0, 0.0
    return defect_count, max_depth


def radial_variation(contour: np.ndarray, cx: float, cy: float) -> float:
    pts = contour.reshape(-1, 2).astype(np.float32)
    if len(pts) == 0:
        return 0.0
    distances = np.sqrt((pts[:, 0] - cx) ** 2 + (pts[:, 1] - cy) ** 2)
    return float(np.std(distances) / (np.mean(distances) + 1e-6))


def contour_features(contour: np.ndarray, diff_blur: np.ndarray) -> Dict[str, Any]:
    area = float(cv2.contourArea(contour))
    perimeter = float(cv2.arcLength(contour, True))
    circularity = 0.0 if perimeter <= 1e-6 else float(4.0 * math.pi * area / (perimeter * perimeter))

    x, y, w, h = cv2.boundingRect(contour)
    rect = cv2.minAreaRect(contour)
    (_, _), (rw, rh), _ = rect
    major_axis = float(max(rw, rh))
    minor_axis = float(min(rw, rh))
    aspect_ratio = major_axis / minor_axis if minor_axis > 1e-6 else 999.0
    rect_area = major_axis * minor_axis
    rectangularity = area / rect_area if rect_area > 1e-6 else 0.0

    hull = cv2.convexHull(contour)
    hull_area = float(cv2.contourArea(hull))
    hull_perimeter = float(cv2.arcLength(hull, True))
    solidity = area / hull_area if hull_area > 1e-6 else 0.0
    hull_roughness = perimeter / hull_perimeter if hull_perimeter > 1e-6 else 1.0

    approx = cv2.approxPolyDP(contour, 0.04 * perimeter if perimeter > 1e-6 else 1.0, True)
    vertex_count = int(len(approx))

    moments = cv2.moments(contour)
    if abs(moments["m00"]) > 1e-9:
        cx = float(moments["m10"] / moments["m00"])
        cy = float(moments["m01"] / moments["m00"])
    else:
        (cx, cy), _, _ = rect
        cx, cy = float(cx), float(cy)

    pca_major, pca_minor, pca_ratio = pca_features(contour)
    defects, max_defect_depth = convexity_defect_features(contour)
    radial_cv = radial_variation(contour, cx, cy)
    equivalent_diameter = math.sqrt(4.0 * area / math.pi) if area > 0 else 0.0

    object_mask = np.zeros_like(diff_blur, dtype=np.uint8)
    cv2.drawContours(object_mask, [contour], -1, 255, thickness=cv2.FILLED)
    mean_diff = float(cv2.mean(diff_blur, mask=object_mask)[0])
    confidence_score = area * max(mean_diff, 1.0)

    return {
        "area_px": area,
        "perimeter_px": perimeter,
        "circularity": circularity,
        "bbox_x": int(x),
        "bbox_y": int(y),
        "bbox_w": int(w),
        "bbox_h": int(h),
        "centroid_x_px": cx,
        "centroid_y_px": cy,
        "major_axis_px": major_axis,
        "minor_axis_px": minor_axis,
        "aspect_ratio": aspect_ratio,
        "rectangularity": rectangularity,
        "solidity": solidity,
        "hull_roughness": hull_roughness,
        "vertex_count": vertex_count,
        "pca_major_px": pca_major,
        "pca_minor_px": pca_minor,
        "pca_ratio": pca_ratio,
        "convexity_defect_count": defects,
        "max_defect_depth_px": max_defect_depth,
        "radial_cv": radial_cv,
        "equivalent_diameter_px": equivalent_diameter,
        "mean_diff": mean_diff,
        "confidence_score": confidence_score,
        "rect": rect,
    }


def classify_three_shapes(features: Dict[str, Any], cfg: DetectorConfig) -> Tuple[str, str]:
    elongated = features["aspect_ratio"] >= cfg.rod_aspect_min or features["pca_ratio"] >= cfg.rod_pca_ratio_min
    long_enough = max(features["major_axis_px"], features["pca_major_px"]) >= cfg.min_rod_length_px
    pca_minor = features["pca_minor_px"] if features["pca_minor_px"] > 0 else features["minor_axis_px"]
    narrow_enough = min(features["minor_axis_px"], pca_minor) <= cfg.max_rod_width_px
    rectangular_enough = features["rectangularity"] >= cfg.rod_rectangularity_min
    compact_guard = features["aspect_ratio"] <= cfg.circle_compact_aspect_max and features["pca_ratio"] <= cfg.circle_compact_pca_max

    if elongated and long_enough and narrow_enough and rectangular_enough and not compact_guard:
        return "rod", "long_narrow_elongated"

    star_by_defects = (
        features["convexity_defect_count"] >= cfg.star_defects_min
        and features["max_defect_depth_px"] >= cfg.star_min_defect_depth_px
        and features["solidity"] <= cfg.star_solidity_max
        and features["radial_cv"] >= cfg.star_radial_cv_min
    )
    star_by_rough_hull = (
        features["convexity_defect_count"] >= cfg.star_defects_min + 1
        and features["hull_roughness"] >= cfg.star_hull_roughness_min
        and features["solidity"] <= cfg.star_solidity_max + 0.08
    )

    if star_by_defects or star_by_rough_hull:
        return "irregular", "star_like_concave"

    return "circle", "circle_fallback_not_rod_not_star"


# =============================================================================
# Drawing and outputs from the proven script, adapted to GUI return format
# =============================================================================

def draw_detection(img: np.ndarray, contour: np.ndarray, features: Dict[str, Any], shape: str,
                   obj_id: int, mm_per_px_x: float, mm_per_px_y: float, draw_labels: bool) -> None:
    avg_mm_per_px = (mm_per_px_x + mm_per_px_y) / 2.0

    green = (0, 255, 0)
    cyan = (255, 255, 0)
    orange = (0, 165, 255)
    white = (255, 255, 255)

    label_x = int(max(0, features["bbox_x"]))
    label_y = int(max(12, features["bbox_y"] - 5))

    if shape == "circle":
        (x, y), r = cv2.minEnclosingCircle(contour)
        center = (int(round(x)), int(round(y)))
        radius = int(round(r))
        cv2.circle(img, center, radius, green, 2)
        diameter_mm = 2.0 * float(r) * avg_mm_per_px
        label = f"{obj_id}:circle {diameter_mm:.1f}mm"
        color = green
        label_x = int(max(0, x - r))
    elif shape == "rod":
        rect = features["rect"]
        box = cv2.boxPoints(rect)
        box = np.int32(np.round(box))
        cv2.drawContours(img, [box], 0, cyan, 2)
        (cx, cy), (rw, rh), angle = rect
        if rw < rh:
            angle += 90.0
        theta = math.radians(angle)
        half = features["major_axis_px"] / 2.0
        dx = math.cos(theta) * half
        dy = math.sin(theta) * half
        p1 = (int(round(cx - dx)), int(round(cy - dy)))
        p2 = (int(round(cx + dx)), int(round(cy + dy)))
        cv2.line(img, p1, p2, cyan, 2)
        length_mm = features["major_axis_px"] * avg_mm_per_px
        width_mm = features["minor_axis_px"] * avg_mm_per_px
        label = f"{obj_id}:rod {length_mm:.1f}x{width_mm:.1f}mm"
        color = cyan
    else:
        cv2.drawContours(img, [contour], -1, orange, 2)
        eq_mm = features["equivalent_diameter_px"] * avg_mm_per_px
        label = f"{obj_id}:irregular {eq_mm:.1f}mm"
        color = orange

    centroid = (int(round(features["centroid_x_px"])), int(round(features["centroid_y_px"])))
    cv2.circle(img, centroid, 2, white, -1)

    if draw_labels:
        cv2.putText(img, label, (label_x, label_y), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: List[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def process_image(image_path: Path, background_filename: Optional[str] = None, data_string_override: Optional[str] = None) -> Dict[str, Any]:
    color = safe_read_color(image_path)
    gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)

    run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    run_dir = OUTPUT_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Create the same output organisation as the proven command-line detector.
    dirs = {
        "grayscale": run_dir / "01_grayscale",
        "difference": run_dir / "02_difference_map",
        "edges": run_dir / "03_edges",
        "mask": run_dir / "04_particle_mask",
        "overlay": run_dir / "05_annotated_overlay",
        "preview": run_dir / "06_preview_grid",
        "debug": run_dir / "07_debug",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    height, width = gray.shape[:2]
    mm_per_px_x = CFG.fov_width_mm / float(width)
    mm_per_px_y = CFG.fov_height_mm / float(height)
    area_per_px_mm2 = mm_per_px_x * mm_per_px_y
    avg_mm_per_px = (mm_per_px_x + mm_per_px_y) / 2.0

    background, background_source, bg_count = build_background_model(background_filename, image_path, gray.shape)
    if background is None:
        # Fallback only keeps the GUI usable; normal use should place real backgrounds in background_images/.
        background = cv2.GaussianBlur(gray, (0, 0), sigmaX=21, sigmaY=21)
        background_stage_name = "3. Estimated background"
    else:
        background_stage_name = "3. Median/selected background reference"

    cv2.imwrite(str(run_dir / "median_background.png"), background)

    diff_raw, diff_blur = make_difference_map(gray, background, CFG.diff_sigma)
    threshold_value, region_mask = threshold_difference(diff_blur, CFG.min_diff_threshold)
    edges, filled_edges, particle_mask = make_particle_mask(diff_blur, region_mask, CFG)

    contours, _ = cv2.findContours(particle_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    detections: List[Dict[str, Any]] = []

    for contour in contours:
        features = contour_features(contour, diff_blur)
        if features["area_px"] < CFG.min_area_px:
            continue
        if features["area_px"] > CFG.max_area_px:
            continue
        if component_inside_top_right_exclusion(features, gray.shape, CFG):
            continue
        shape, reason = classify_three_shapes(features, CFG)
        detections.append({"contour": contour, "features": features, "shape": shape, "reason": reason})

    detections.sort(key=lambda d: d["features"]["confidence_score"], reverse=True)
    if CFG.expected_particles and CFG.expected_particles > 0:
        detections = detections[:CFG.expected_particles]

    detections.sort(key=lambda d: (d["features"]["centroid_y_px"], d["features"]["centroid_x_px"]))

    annotated = color.copy()
    particles: List[Dict[str, Any]] = []
    csv_rows: List[Dict[str, Any]] = []
    counts = {"circle": 0, "rod": 0, "irregular": 0}
    area_sums = {"circle": 0.0, "rod": 0.0, "irregular": 0.0}

    display_names = {"circle": "Circular", "rod": "Rod-like", "irregular": "Irregular"}

    for obj_id, det in enumerate(detections, start=1):
        features = det["features"]
        shape = det["shape"]
        contour = det["contour"]

        draw_detection(annotated, contour, features, shape, obj_id, mm_per_px_x, mm_per_px_y, draw_labels=not CFG.no_labels)

        area_mm2 = features["area_px"] * area_per_px_mm2

        if shape == "circle":
            (_, _), radius = cv2.minEnclosingCircle(contour)
            primary_size_mm = 2.0 * float(radius) * avg_mm_per_px
            primary_size_name = "circle_diameter_mm"
        elif shape == "rod":
            primary_size_mm = features["major_axis_px"] * avg_mm_per_px
            primary_size_name = "rod_length_mm"
        else:
            primary_size_mm = features["equivalent_diameter_px"] * avg_mm_per_px
            primary_size_name = "irregular_equiv_diameter_mm"

        counts[shape] += 1
        area_sums[shape] += area_mm2

        gui_row = {
            "id": obj_id,
            "class": display_names[shape],
            "centroid_x_px": round(features["centroid_x_px"], 2),
            "centroid_y_px": round(features["centroid_y_px"], 2),
            "area_px": round(features["area_px"], 2),
            "area_mm2": round(area_mm2, 2),
            "equivalent_diameter_mm": round(features["equivalent_diameter_px"] * avg_mm_per_px, 2),
            "primary_size_name": primary_size_name,
            "primary_size_mm": round(primary_size_mm, 2),
            "perimeter_px": round(features["perimeter_px"], 2),
            "aspect_ratio": round(features["aspect_ratio"], 2),
            "pca_ratio": round(features["pca_ratio"], 2),
            "circularity": round(features["circularity"], 3),
            "solidity": round(features["solidity"], 3),
            "rectangularity": round(features["rectangularity"], 3),
            "radial_cv": round(features["radial_cv"], 3),
        }
        particles.append(gui_row)

        csv_rows.append({
            "image_name": image_path.name,
            "object_id": obj_id,
            "shape_class": shape,
            "display_class": display_names[shape],
            "classification_reason": det["reason"],
            **gui_row,
            "equivalent_diameter_px": round(features["equivalent_diameter_px"], 2),
            "major_axis_px": round(features["major_axis_px"], 2),
            "minor_axis_px": round(features["minor_axis_px"], 2),
            "major_axis_mm": round(features["major_axis_px"] * avg_mm_per_px, 4),
            "minor_axis_mm": round(features["minor_axis_px"] * avg_mm_per_px, 4),
            "convexity_defect_count": features["convexity_defect_count"],
            "max_defect_depth_px": round(features["max_defect_depth_px"], 3),
            "hull_roughness": round(features["hull_roughness"], 4),
            "mean_diff": round(features["mean_diff"], 3),
            "diff_threshold_used": threshold_value,
        })

    counts_gui = {
        "total": len(particles),
        "circular": counts["circle"],
        "rod_like": counts["rod"],
        "irregular": counts["irregular"],
    }
    total_area_mm2 = round(sum(float(p["area_mm2"]) for p in particles), 2)

    metadata = parse_data_string(data_string_override) if data_string_override else load_metadata(image_path)
    metadata["background_source"] = background_source
    metadata["background_image_count"] = bg_count
    metadata["exclusion_policy"] = "Only the fixed top-right LED/reflection artefact is excluded"
    metadata["classification_policy"] = "Rod if clearly elongated; irregular only if star-like; otherwise circle"

    run_config = {
        **CFG.__dict__,
        "image_width_px": width,
        "image_height_px": height,
        "mm_per_px_x": mm_per_px_x,
        "mm_per_px_y": mm_per_px_y,
        "area_per_px_mm2": area_per_px_mm2,
        "background_source": background_source,
        "background_image_count": bg_count,
    }
    (run_dir / "run_config.json").write_text(json.dumps(run_config, indent=2), encoding="utf-8")

    stem = image_path.stem
    cv2.imwrite(str(dirs["grayscale"] / f"{stem}_01_grayscale.png"), gray)
    cv2.imwrite(str(dirs["difference"] / f"{stem}_02_difference_map.png"), diff_blur)
    cv2.imwrite(str(dirs["edges"] / f"{stem}_03_edges.png"), edges)
    cv2.imwrite(str(dirs["mask"] / f"{stem}_04_particle_mask.png"), particle_mask)
    cv2.imwrite(str(dirs["overlay"] / f"{stem}_05_annotated_overlay.png"), annotated)

    debug_stack = np.hstack([
        cv2.cvtColor(diff_raw, cv2.COLOR_GRAY2BGR),
        cv2.cvtColor(diff_blur, cv2.COLOR_GRAY2BGR),
        cv2.cvtColor(filled_edges, cv2.COLOR_GRAY2BGR),
    ])
    cv2.imwrite(str(dirs["debug"] / f"{stem}_07_debug_stack.png"), debug_stack)

    measurements_path = run_dir / "three_shape_measurements.csv"
    summary_path = run_dir / "three_shape_summary.csv"
    write_csv(measurements_path, csv_rows)
    write_csv(summary_path, [{
        "image_name": image_path.name,
        "circle_count": counts["circle"],
        "rod_count": counts["rod"],
        "irregular_count": counts["irregular"],
        "total_count": len(particles),
        "circle_total_area_mm2": round(area_sums["circle"], 4),
        "rod_total_area_mm2": round(area_sums["rod"], 4),
        "irregular_total_area_mm2": round(area_sums["irregular"], 4),
        "total_particle_area_mm2": round(sum(area_sums.values()), 4),
        "coverage_percent_of_85x85_fov": round(100.0 * sum(area_sums.values()) / (CFG.fov_width_mm * CFG.fov_height_mm), 4),
    }])

    # Return the same stage list expected by the existing JavaScript.
    stages = [
        {"name": "1. Original image", "key": "original", "image": save_stage(run_dir, "01_original", color)},
        {"name": "2. Grayscale conversion", "key": "grayscale", "image": save_stage(run_dir, "02_grayscale", gray)},
        {"name": background_stage_name, "key": "background", "image": save_stage(run_dir, "03_background", background)},
        {"name": "4. Difference map", "key": "difference", "image": save_stage(run_dir, "04_difference_map", diff_blur)},
        {"name": "5. Edge detection", "key": "edges", "image": save_stage(run_dir, "05_edges", edges)},
        {"name": "6. Region mask", "key": "region", "image": save_stage(run_dir, "06_region_mask", region_mask)},
        {"name": "7. Particle mask", "key": "mask", "image": save_stage(run_dir, "07_particle_mask", particle_mask)},
        {"name": "8. Annotated result", "key": "annotated", "image": save_stage(run_dir, "08_annotated", annotated)},
    ]

    summary = {
        "image_name": image_path.name,
        "run_id": run_id,
        "processing_scale": f"{avg_mm_per_px:.3f} mm/pixel",
        "background_source": background_source,
        "counts": counts_gui,
        "total_area_mm2": total_area_mm2,
        "csv_url": f"/outputs/{run_id}/three_shape_measurements.csv",
    }

    return {"summary": summary, "metadata": metadata, "stages": stages, "particles": particles}


# =============================================================================
# Flask routes
# =============================================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/images")
def api_images():
    return jsonify({"images": list_images()})


@app.route("/api/backgrounds")
def api_backgrounds():
    return jsonify({"backgrounds": list_backgrounds()})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    file = request.files.get("image")
    if not file or file.filename == "":
        return jsonify({"error": "No image was uploaded."}), 400
    if not allowed_image(file.filename):
        return jsonify({"error": "Unsupported image type."}), 400
    filename = secure_filename(file.filename)
    destination = CAPTURED_DIR / filename
    file.save(destination)
    return jsonify({"message": "Image uploaded successfully.", "filename": filename, "images": list_images()})


@app.route("/api/upload-background", methods=["POST"])
def api_upload_background():
    file = request.files.get("background")
    if not file or file.filename == "":
        return jsonify({"error": "No background image was uploaded."}), 400
    if not allowed_image(file.filename):
        return jsonify({"error": "Unsupported background image type."}), 400
    filename = secure_filename(file.filename)
    destination = BACKGROUND_DIR / filename
    file.save(destination)
    return jsonify({"message": "Background uploaded successfully.", "filename": filename, "backgrounds": list_backgrounds()})


@app.route("/api/process", methods=["POST"])
def api_process():
    data = request.get_json(force=True)
    filename = secure_filename(data.get("filename", ""))
    background_filename = data.get("background_filename") or "AUTO"
    data_string_override = (data.get("data_string") or "").strip() or None

    image_path = CAPTURED_DIR / filename
    if not filename or not image_path.exists() or not allowed_image(filename):
        return jsonify({"error": "Select a valid image from captured_images first."}), 400

    try:
        return jsonify(process_image(image_path, background_filename=background_filename, data_string_override=data_string_override))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/captured_images/<path:filename>")
def captured_file(filename: str):
    return send_from_directory(CAPTURED_DIR, filename)


@app.route("/background_images/<path:filename>")
def background_file(filename: str):
    return send_from_directory(BACKGROUND_DIR, filename)


@app.route("/outputs/<path:filename>")
def output_file(filename: str):
    return send_from_directory(OUTPUT_DIR, filename)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
