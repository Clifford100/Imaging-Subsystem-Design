#!/usr/bin/env python3
"""
Particle analysis pipeline for the ESP32-CAM Imaging Subsystem.

This script compares captured particle images against background images,
segments particles using grayscale difference and edge information, classifies
particles as circle, rod or irregular, and exports measurement tables and
visual debugging outputs.

The pipeline is designed for repeatable analysis runs:
- input images and background images are selected using command-line arguments;
- output folders are generated dynamically for every run;
- filenames use clean research/reporting names rather than temporary test labels;
- shape measurements are exported to CSV for report tables and further analysis.

Classes:
    circle      -> compact particles, including imperfect circles, ovals,
                   partial circles and semi-circles
    rod         -> long, narrow particles/sticks
    irregular   -> concave or rough particles that are not circle/rod-like

Detection uses grayscale information only. Colour images are used only for
annotated visual outputs.
"""

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Dict, List, Tuple

import cv2
import numpy as np


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


# =============================================================================
# Arguments
# =============================================================================

def parse_args():
    p = argparse.ArgumentParser(
        description="Particle detector for circle, rod and irregular particles"
    )

    p.add_argument("--input-dir", required=True, help="Folder containing particle test images")
    p.add_argument("--background-dir", required=True, help="Folder containing no-particle background images")
    p.add_argument("--output-dir", required=True, help="Output folder")

    p.add_argument("--fov-width-mm", type=float, required=True, help="Physical width represented by full image")
    p.add_argument("--fov-height-mm", type=float, required=True, help="Physical height represented by full image")

    # Segmentation parameters
    p.add_argument("--min-area-px", type=float, default=45.0)
    p.add_argument("--max-area-px", type=float, default=12000.0)
    p.add_argument("--diff-sigma", type=float, default=3.0)
    p.add_argument("--min-diff-threshold", type=int, default=8)
    p.add_argument("--canny-low", type=int, default=20)
    p.add_argument("--canny-high", type=int, default=60)
    p.add_argument("--edge-dilate-k", type=int, default=3)
    p.add_argument("--close-k", type=int, default=5)
    p.add_argument("--open-k", type=int, default=3)

    # Optional: useful when testing exactly 3 physical objects.
    p.add_argument(
        "--expected-particles",
        type=int,
        default=0,
        help="If >0, keep only this many strongest detections per image"
    )

    # Rod rules. Rods must be clearly long and narrow.
    p.add_argument("--rod-aspect-min", type=float, default=2.35)
    p.add_argument("--rod-pca-ratio-min", type=float, default=2.45)
    p.add_argument("--min-rod-length-px", type=float, default=22.0)
    p.add_argument("--max-rod-width-px", type=float, default=18.0)
    p.add_argument("--rod-rectangularity-min", type=float, default=0.25)

    # Irregular shape rules.
    p.add_argument("--irregular-defects-min", type=int, default=3)
    p.add_argument("--irregular-solidity-max", type=float, default=0.86)
    p.add_argument("--irregular-radial-cv-min", type=float, default=0.12)
    p.add_argument("--irregular-min-defect-depth-px", type=float, default=1.5)
    p.add_argument("--irregular-hull-roughness-min", type=float, default=1.05)

    # Circle fallback. Anything compact and not irregular/rod becomes circle.
    p.add_argument("--circle-compact-aspect-max", type=float, default=2.20)
    p.add_argument("--circle-compact-pca-max", type=float, default=2.45)

    # Only ignore the fixed top-right LED/reflection artefact.
    p.add_argument(
        "--ignore-top-right-corner-px",
        type=int,
        default=34,
        help="Ignore only the fixed top-right LED/reflection artefact. Set 0 to disable."
    )

    p.add_argument("--no-labels", action="store_true", help="Draw shapes without text labels")

    return p.parse_args()


# =============================================================================
# Basic utilities
# =============================================================================

def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def list_images(folder: Path) -> List[Path]:
    return sorted([p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS])


def read_color(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not read image: {path}")
    return img


def read_gray(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"Could not read image: {path}")
    return img


def odd_kernel_from_sigma(sigma: float) -> int:
    k = max(3, int(round(4 * sigma + 1)))
    return k if k % 2 == 1 else k + 1


def build_background_model(bg_dir: Path) -> Tuple[np.ndarray, int]:
    files = list_images(bg_dir)
    if not files:
        raise ValueError(f"No background images found in: {bg_dir}")

    stack = []
    base_shape = None

    for file in files:
        gray = read_gray(file)

        if base_shape is None:
            base_shape = gray.shape
        elif gray.shape != base_shape:
            gray = cv2.resize(
                gray,
                (base_shape[1], base_shape[0]),
                interpolation=cv2.INTER_AREA
            )

        stack.append(gray)

    background = np.median(np.stack(stack, axis=0), axis=0).astype(np.uint8)
    return background, len(files)


# =============================================================================
# Top-right LED/reflection exclusion
# =============================================================================

def apply_exclusion_zones(mask: np.ndarray, args) -> np.ndarray:
    """
    Remove only the fixed top-right LED/reflection artefact.

    The other edges/corners are not removed because real rods may appear there.
    """
    cleaned = mask.copy()
    h, w = cleaned.shape[:2]

    top_right = max(0, int(args.ignore_top_right_corner_px))

    if top_right > 0:
        cleaned[0:top_right, max(0, w - top_right):w] = 0

    return cleaned


def component_inside_top_right_exclusion(features: Dict, image_shape: Tuple[int, int], args) -> bool:
    """
    Reject a component only if its centroid lies inside the ignored top-right region.
    """
    h, w = image_shape[:2]

    cx = float(features["centroid_x_px"])
    cy = float(features["centroid_y_px"])

    top_right = max(0, int(args.ignore_top_right_corner_px))

    if top_right > 0 and cx > (w - top_right) and cy < top_right:
        return True

    return False


# =============================================================================
# Segmentation
# =============================================================================

def make_difference_map(
    gray: np.ndarray,
    background: np.ndarray,
    sigma: float
) -> Tuple[np.ndarray, np.ndarray]:

    if background.shape != gray.shape:
        background = cv2.resize(
            background,
            (gray.shape[1], gray.shape[0]),
            interpolation=cv2.INTER_AREA
        )

    diff_raw = cv2.absdiff(gray, background)

    k = odd_kernel_from_sigma(sigma)
    diff_blur = cv2.GaussianBlur(
        diff_raw,
        (k, k),
        sigmaX=sigma,
        sigmaY=sigma
    )

    return diff_raw, diff_blur


def threshold_difference(
    diff_blur: np.ndarray,
    min_threshold: int
) -> Tuple[int, np.ndarray]:

    otsu_threshold, _ = cv2.threshold(
        diff_blur,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )

    threshold_value = max(int(otsu_threshold), int(min_threshold))

    _, region_mask = cv2.threshold(
        diff_blur,
        threshold_value,
        255,
        cv2.THRESH_BINARY
    )

    return threshold_value, region_mask


def fill_edge_contours(edge_mask: np.ndarray) -> np.ndarray:
    contours, _ = cv2.findContours(
        edge_mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    filled = np.zeros_like(edge_mask)

    if contours:
        cv2.drawContours(
            filled,
            contours,
            -1,
            255,
            thickness=cv2.FILLED
        )

    return filled


def make_particle_mask(
    diff_blur: np.ndarray,
    region_mask: np.ndarray,
    args
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:

    edges = cv2.Canny(
        diff_blur,
        args.canny_low,
        args.canny_high
    )

    if args.edge_dilate_k > 1:
        k = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (args.edge_dilate_k, args.edge_dilate_k)
        )
        edges = cv2.dilate(edges, k, iterations=1)

    filled_edges = fill_edge_contours(edges)

    combined = cv2.bitwise_or(region_mask, filled_edges)

    # Remove only top-right LED/reflection artefact.
    combined = apply_exclusion_zones(combined, args)

    if args.close_k > 1:
        k = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (args.close_k, args.close_k)
        )
        combined = cv2.morphologyEx(
            combined,
            cv2.MORPH_CLOSE,
            k,
            iterations=1
        )

    if args.open_k > 1:
        k = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (args.open_k, args.open_k)
        )
        combined = cv2.morphologyEx(
            combined,
            cv2.MORPH_OPEN,
            k,
            iterations=1
        )

    # Apply again after morphology because closing can regrow pixels near the corner.
    combined = apply_exclusion_zones(combined, args)

    return edges, filled_edges, combined


# =============================================================================
# Shape features
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

    distances = np.sqrt(
        (pts[:, 0] - cx) ** 2 +
        (pts[:, 1] - cy) ** 2
    )

    return float(np.std(distances) / (np.mean(distances) + 1e-6))


def contour_features(contour: np.ndarray, diff_blur: np.ndarray) -> Dict:
    area = float(cv2.contourArea(contour))
    perimeter = float(cv2.arcLength(contour, True))

    circularity = (
        0.0
        if perimeter <= 1e-6
        else float(4.0 * math.pi * area / (perimeter * perimeter))
    )

    x, y, w, h = cv2.boundingRect(contour)

    rect = cv2.minAreaRect(contour)
    (_, _), (rw, rh), _ = rect

    major_axis = float(max(rw, rh))
    minor_axis = float(min(rw, rh))

    aspect_ratio = (
        major_axis / minor_axis
        if minor_axis > 1e-6
        else 999.0
    )

    rect_area = major_axis * minor_axis

    rectangularity = (
        area / rect_area
        if rect_area > 1e-6
        else 0.0
    )

    hull = cv2.convexHull(contour)
    hull_area = float(cv2.contourArea(hull))
    hull_perimeter = float(cv2.arcLength(hull, True))

    solidity = (
        area / hull_area
        if hull_area > 1e-6
        else 0.0
    )

    hull_roughness = (
        perimeter / hull_perimeter
        if hull_perimeter > 1e-6
        else 1.0
    )

    approx = cv2.approxPolyDP(
        contour,
        0.04 * perimeter if perimeter > 1e-6 else 1.0,
        True
    )

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

    equivalent_diameter = (
        math.sqrt(4.0 * area / math.pi)
        if area > 0
        else 0.0
    )

    object_mask = np.zeros_like(diff_blur, dtype=np.uint8)

    cv2.drawContours(
        object_mask,
        [contour],
        -1,
        255,
        thickness=cv2.FILLED
    )

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


# =============================================================================
# Shape classification
# =============================================================================

def classify_particle_shape(features: Dict, args) -> Tuple[str, str]:
    """
    Return:
        shape_class, reason

    Rule:
        1. clear long narrow object -> rod
        2. irregular concave object -> irregular
        3. everything else -> circle

    This is intentional because your circles are often imperfect, oval or partial
    under poor lighting.
    """

    # -------------------------------------------------------------------------
    # 1. Rod detection
    # -------------------------------------------------------------------------
    elongated = (
        features["aspect_ratio"] >= args.rod_aspect_min
        or features["pca_ratio"] >= args.rod_pca_ratio_min
    )

    long_enough = max(
        features["major_axis_px"],
        features["pca_major_px"]
    ) >= args.min_rod_length_px

    narrow_enough = min(
        features["minor_axis_px"],
        features["pca_minor_px"]
        if features["pca_minor_px"] > 0
        else features["minor_axis_px"],
    ) <= args.max_rod_width_px

    rectangular_enough = (
        features["rectangularity"] >= args.rod_rectangularity_min
    )

    # Prevent oval/circle-like objects from becoming rods.
    compact_guard = (
        features["aspect_ratio"] <= args.circle_compact_aspect_max
        and features["pca_ratio"] <= args.circle_compact_pca_max
    )

    if elongated and long_enough and narrow_enough and rectangular_enough and not compact_guard:
        return "rod", "long_narrow_elongated"

    # -------------------------------------------------------------------------
    # 2. Irregular detection
    # -------------------------------------------------------------------------
    irregular_by_defects = (
        features["convexity_defect_count"] >= args.irregular_defects_min
        and features["max_defect_depth_px"] >= args.irregular_min_defect_depth_px
        and features["solidity"] <= args.irregular_solidity_max
        and features["radial_cv"] >= args.irregular_radial_cv_min
    )

    irregular_by_rough_hull = (
        features["convexity_defect_count"] >= args.irregular_defects_min + 1
        and features["hull_roughness"] >= args.irregular_hull_roughness_min
        and features["solidity"] <= args.irregular_solidity_max + 0.08
    )

    if irregular_by_defects or irregular_by_rough_hull:
        return "irregular", "irregular_concave"

    # -------------------------------------------------------------------------
    # 3. Circle fallback
    # -------------------------------------------------------------------------
    return "circle", "circle_fallback_not_rod_not_irregular"


# =============================================================================
# Drawing and outputs
# =============================================================================

def draw_detection(
    img: np.ndarray,
    contour: np.ndarray,
    features: Dict,
    shape: str,
    obj_id: int,
    mm_per_px_x: float,
    mm_per_px_y: float,
    draw_labels: bool
):
    avg_mm_per_px = (mm_per_px_x + mm_per_px_y) / 2.0

    green = (0, 255, 0)
    cyan = (255, 255, 0)
    orange = (0, 165, 255)
    white = (255, 255, 255)

    label_x = int(max(0, features["bbox_x"]))
    label_y = int(max(12, features["bbox_y"] - 5))

    if shape == "circle":
        # Enclosing circle works better for partial / semi-circle masks.
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

    centroid = (
        int(round(features["centroid_x_px"])),
        int(round(features["centroid_y_px"]))
    )

    cv2.circle(img, centroid, 2, white, -1)

    if draw_labels:
        cv2.putText(
            img,
            label,
            (label_x, label_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            color,
            1,
            cv2.LINE_AA
        )


def save_preview_grid(
    out_path: Path,
    images: List[np.ndarray],
    titles: List[str]
):
    tiles = []

    for i, im in enumerate(images):
        if im.ndim == 2:
            vis = cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)
        else:
            vis = im.copy()

        vis = cv2.resize(
            vis,
            (240, 240),
            interpolation=cv2.INTER_AREA
        )

        if i < len(titles):
            cv2.putText(
                vis,
                titles[i],
                (7, 18),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                (0, 255, 255),
                1,
                cv2.LINE_AA
            )

        tiles.append(vis)

    while len(tiles) < 6:
        tiles.append(np.zeros((240, 240, 3), dtype=np.uint8))

    grid = np.vstack([
        np.hstack(tiles[:3]),
        np.hstack(tiles[3:6])
    ])

    cv2.imwrite(str(out_path), grid)


def write_csv(path: Path, rows: List[Dict]):
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    fieldnames = []

    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# =============================================================================
# Main
# =============================================================================

def main():
    args = parse_args()

    input_dir = Path(args.input_dir)
    background_dir = Path(args.background_dir)
    output_dir = Path(args.output_dir)

    dirs = {
        "grayscale": output_dir / "grayscale",
        "difference": output_dir / "difference_map",
        "edges": output_dir / "edges",
        "mask": output_dir / "particle_mask",
        "overlay": output_dir / "annotated_overlay",
        "preview": output_dir / "preview_grid",
        "debug": output_dir / "debug",
    }

    for d in dirs.values():
        ensure_dir(d)

    background, bg_count = build_background_model(background_dir)

    cv2.imwrite(
        str(output_dir / "median_background.png"),
        background
    )

    image_files = list_images(input_dir)

    if not image_files:
        raise ValueError(f"No images found in: {input_dir}")

    first = read_color(image_files[0])

    height, width = first.shape[:2]

    mm_per_px_x = args.fov_width_mm / float(width)
    mm_per_px_y = args.fov_height_mm / float(height)
    area_per_px_mm2 = mm_per_px_x * mm_per_px_y

    run_config = vars(args).copy()

    run_config.update({
        "image_width_px": width,
        "image_height_px": height,
        "mm_per_px_x": mm_per_px_x,
        "mm_per_px_y": mm_per_px_y,
        "area_per_px_mm2": area_per_px_mm2,
        "background_image_count": bg_count,
        "classification_policy": (
            "rod if clearly elongated; irregular if concave/rough; "
            "otherwise circle including imperfect/oval/semi-circle"
        ),
        "exclusion_policy": (
            "only the fixed top-right LED/reflection artefact is excluded"
        ),
    })

    (output_dir / "run_config.json").write_text(
        json.dumps(run_config, indent=2),
        encoding="utf-8"
    )

    all_measurements = []
    summaries = []

    for image_index, image_path in enumerate(image_files, start=1):
        stem = image_path.stem

        color = read_color(image_path)
        gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)

        diff_raw, diff_blur = make_difference_map(
            gray,
            background,
            args.diff_sigma
        )

        threshold_value, region_mask = threshold_difference(
            diff_blur,
            args.min_diff_threshold
        )

        edges, filled_edges, particle_mask = make_particle_mask(
            diff_blur,
            region_mask,
            args
        )

        contours, _ = cv2.findContours(
            particle_mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )

        detections = []

        for contour in contours:
            features = contour_features(contour, diff_blur)

            if features["area_px"] < args.min_area_px:
                continue

            if features["area_px"] > args.max_area_px:
                continue

            if component_inside_top_right_exclusion(features, gray.shape, args):
                continue

            shape, reason = classify_particle_shape(features, args)

            detections.append({
                "contour": contour,
                "features": features,
                "shape": shape,
                "reason": reason,
            })

        # Optional controlled-test filter.
        detections.sort(
            key=lambda d: d["features"]["confidence_score"],
            reverse=True
        )

        if args.expected_particles and args.expected_particles > 0:
            detections = detections[:args.expected_particles]

        # Stable numbering: top-to-bottom, then left-to-right.
        detections.sort(
            key=lambda d: (
                d["features"]["centroid_y_px"],
                d["features"]["centroid_x_px"]
            )
        )

        annotated = color.copy()

        counts = {
            "circle": 0,
            "rod": 0,
            "irregular": 0
        }

        area_sums = {
            "circle": 0.0,
            "rod": 0.0,
            "irregular": 0.0
        }

        for obj_id, det in enumerate(detections, start=1):
            features = det["features"]
            shape = det["shape"]
            contour = det["contour"]

            draw_detection(
                annotated,
                contour,
                features,
                shape,
                obj_id,
                mm_per_px_x,
                mm_per_px_y,
                draw_labels=not args.no_labels
            )

            area_mm2 = features["area_px"] * area_per_px_mm2
            avg_mm_per_px = (mm_per_px_x + mm_per_px_y) / 2.0

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

            row = {
                "image_index": image_index,
                "image_name": image_path.name,
                "object_id": obj_id,
                "shape_class": shape,
                "classification_reason": det["reason"],
                "centroid_x_px": round(features["centroid_x_px"], 2),
                "centroid_y_px": round(features["centroid_y_px"], 2),
                "area_px": round(features["area_px"], 2),
                "area_mm2": round(area_mm2, 4),
                "primary_size_name": primary_size_name,
                "primary_size_mm": round(primary_size_mm, 4),
                "equivalent_diameter_px": round(features["equivalent_diameter_px"], 2),
                "equivalent_diameter_mm": round(features["equivalent_diameter_px"] * avg_mm_per_px, 4),
                "major_axis_px": round(features["major_axis_px"], 2),
                "minor_axis_px": round(features["minor_axis_px"], 2),
                "major_axis_mm": round(features["major_axis_px"] * avg_mm_per_px, 4),
                "minor_axis_mm": round(features["minor_axis_px"] * avg_mm_per_px, 4),
                "aspect_ratio": round(features["aspect_ratio"], 4),
                "pca_ratio": round(features["pca_ratio"], 4),
                "circularity": round(features["circularity"], 4),
                "solidity": round(features["solidity"], 4),
                "rectangularity": round(features["rectangularity"], 4),
                "radial_cv": round(features["radial_cv"], 4),
                "convexity_defect_count": features["convexity_defect_count"],
                "max_defect_depth_px": round(features["max_defect_depth_px"], 3),
                "hull_roughness": round(features["hull_roughness"], 4),
                "mean_diff": round(features["mean_diff"], 3),
                "diff_threshold_used": threshold_value,
            }

            all_measurements.append(row)

        summaries.append({
            "image_index": image_index,
            "image_name": image_path.name,
            "circle_count": counts["circle"],
            "rod_count": counts["rod"],
            "irregular_count": counts["irregular"],
            "total_count": sum(counts.values()),
            "circle_total_area_mm2": round(area_sums["circle"], 4),
            "rod_total_area_mm2": round(area_sums["rod"], 4),
            "irregular_total_area_mm2": round(area_sums["irregular"], 4),
            "total_particle_area_mm2": round(sum(area_sums.values()), 4),
            "coverage_percent_of_85x85_fov": round(
                100.0 * sum(area_sums.values()) /
                (args.fov_width_mm * args.fov_height_mm),
                4
            ),
        })

        cv2.imwrite(
            str(dirs["grayscale"] / f"{stem}_grayscale.png"),
            gray
        )

        cv2.imwrite(
            str(dirs["difference"] / f"{stem}_difference_map.png"),
            diff_blur
        )

        cv2.imwrite(
            str(dirs["edges"] / f"{stem}_edges.png"),
            edges
        )

        cv2.imwrite(
            str(dirs["mask"] / f"{stem}_particle_mask.png"),
            particle_mask
        )

        cv2.imwrite(
            str(dirs["overlay"] / f"{stem}_annotated_overlay.png"),
            annotated
        )

        debug_stack = np.hstack([
            cv2.cvtColor(diff_raw, cv2.COLOR_GRAY2BGR),
            cv2.cvtColor(diff_blur, cv2.COLOR_GRAY2BGR),
            cv2.cvtColor(filled_edges, cv2.COLOR_GRAY2BGR),
        ])

        cv2.imwrite(
            str(dirs["debug"] / f"{stem}_debug_stack.png"),
            debug_stack
        )

        save_preview_grid(
            dirs["preview"] / f"{stem}_preview_grid.png",
            [gray, diff_blur, edges, region_mask, particle_mask, annotated],
            ["grayscale", "difference", "edges", "region", "mask", "overlay"]
        )

        print(
            f"Processed {image_path.name}: "
            f"total={sum(counts.values())}, "
            f"circle={counts['circle']}, "
            f"rod={counts['rod']}, "
            f"irregular={counts['irregular']}"
        )

    write_csv(
        output_dir / "particle_measurements.csv",
        all_measurements
    )

    write_csv(
        output_dir / "image_summary.csv",
        summaries
    )

    print("\nDone.")
    print(f"Measurements: {output_dir / 'particle_measurements.csv'}")
    print(f"Summary:      {output_dir / 'image_summary.csv'}")
    print(f"Overlays:     {dirs['overlay']}")
    print(f"Previews:     {dirs['preview']}")


if __name__ == "__main__":
    main()