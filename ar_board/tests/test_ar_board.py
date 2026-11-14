"""pytest: контракт планшета, детекция, проекция, сглаживание, headless."""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest

import annotate
import make_board
from ar_board import COMPONENT_ORDER, Smoother, detect_board, load_layout, project_components
from ar_board.detect import BoardPose
from ar_board.io_utils import imread, imwrite
from ar_board.layout import save_layout
from ar_board.project import render_overlay
from ar_board.synthetic import corner_errors, make_case


@pytest.fixture(scope="session")
def board_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("board")
    make_board.generate_board(out, "PUMP-001")
    return out


@pytest.fixture(scope="session")
def layout(board_dir: Path) -> dict:
    return load_layout(board_dir / "layout.json")


@pytest.fixture(scope="session")
def board_image(board_dir: Path) -> np.ndarray:
    image = imread(board_dir / "board_A3.png")
    assert image is not None
    return image


def test_png_pdf_layout_contract(board_dir: Path, layout: dict) -> None:
    png = board_dir / "board_A3.png"
    image = imread(png)
    assert image.shape == (3508, 4961, 3)
    assert image.std() > 10, "планшет не должен быть пустым"

    assert layout["version"] == 1
    assert layout["equipment_id"] == "PUMP-001"
    board = layout["board"]
    assert (board["width_px"], board["height_px"]) == (4961, 3508)
    assert (board["width_mm"], board["height_mm"]) == (420.0, 297.0)

    markers = layout["markers"]
    assert [m["id"] for m in markers] == [0, 1, 2, 3]
    for marker in markers:
        assert marker["size_mm"] == 60.0
        assert len(marker["corners_px"]) == 4
        assert len(marker["corners_mm"]) == 4
        for x, y in marker["corners_px"]:
            assert 0 <= x <= 4961 and 0 <= y <= 3508

    assert [c["component_id"] for c in layout["components"]] == [c for c, _ in COMPONENT_ORDER]
    for comp in layout["components"]:
        assert len(comp["polygon_px"]) == 4
        assert len(comp["polygon_norm"]) == 4
        for x, y in comp["polygon_norm"]:
            assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0

    with png.open("rb") as fh:
        header = fh.read(8)
    assert header.startswith(b"\x89PNG")
    if board.get("pdf"):
        pdf = board_dir / board["pdf"]
        assert pdf.read_bytes()[:5] == b"%PDF-"


def test_quiet_zone_is_white(layout: dict, board_image: np.ndarray) -> None:
    offset = round(5 * layout["board"]["width_px"] / layout["board"]["width_mm"])
    for marker in layout["markers"]:
        corners = np.asarray(marker["corners_px"], dtype=int)
        x0, y0 = corners.min(axis=0)
        x1, y1 = corners.max(axis=0)
        probes = [((x0 + x1) // 2, y0 - offset), ((x0 + x1) // 2, y1 + offset),
                  (x0 - offset, (y0 + y1) // 2), (x1 + offset, (y0 + y1) // 2)]
        for x, y in probes:
            patch = board_image[max(0, y - 2):y + 3, max(0, x - 2):x + 3]
            assert patch.min() >= 245, f"quiet zone маркера {marker['id']} не белая: {(x, y)}"


def test_board_detects_itself(layout: dict, board_image: np.ndarray) -> None:
    pose = detect_board(board_image, layout)
    assert pose is not None
    assert pose.ids == [0, 1, 2, 3]
    assert pose.is_valid
    assert pose.reprojection_error < 2.0
    assert pose.coverage > 0.5
    mean_err, max_err = corner_errors(layout, np.eye(3), pose.homography)
    assert mean_err < 2.0 and max_err < 2.5

    half = cv2.resize(board_image, None, fx=0.3, fy=0.3, interpolation=cv2.INTER_AREA)
    scaled = detect_board(half, layout)
    assert scaled is not None and scaled.is_valid and scaled.ids == [0, 1, 2, 3]


@pytest.mark.parametrize("tilt", [10.0, 20.0, 25.0])
def test_synthetic_detection_and_corner_error(layout: dict, board_image: np.ndarray,
                                              tilt: float) -> None:
    rng = np.random.default_rng(int(tilt) + 100)
    case = make_case(layout, board_image, (1280, 960), rng, tilt_deg=tilt,
                     scale_range=(0.55, 0.9))
    pose = detect_board(case["frame"], layout)
    assert pose is not None and pose.is_valid
    assert len(pose.marker_ids) == 4
    assert pose.reprojection_error < 5.0
    mean_err, max_err = corner_errors(layout, case["homography"], pose.homography)
    assert mean_err < 8.0, f"средняя ошибка углов {mean_err:.2f}px при {tilt}°"
    assert max_err < 12.0


def test_smoothing_keeps_accuracy(layout: dict, board_image: np.ndarray) -> None:
    rng = np.random.default_rng(5)
    smoother = Smoother(alpha=0.35)
    case = make_case(layout, board_image, (1280, 960), rng, tilt_deg=20.0)
    pose = detect_board(case["frame"], layout)
    assert pose is not None
    projected = project_components(pose, layout)
    first = smoother.update(projected, timestamp=0.0)
    second = smoother.update(projected, timestamp=0.04)
    assert len(first) == len(second) == len(layout["components"])
    for a, b in zip(first, second):
        assert np.allclose(np.asarray(b["polygon"]), np.asarray(a["polygon"]), atol=1e-6)
        assert b["visible"]


def test_two_markers_is_not_enough(layout: dict, board_image: np.ndarray) -> None:
    damaged = board_image.copy()
    for marker in layout["markers"]:
        if marker["id"] in (2, 3):
            corners = np.asarray(marker["corners_px"], dtype=int)
            x0, y0 = corners.min(axis=0) - 8
            x1, y1 = corners.max(axis=0) + 8
            cv2.rectangle(damaged, (int(x0), int(y0)), (int(x1), int(y1)), (255, 255, 255), -1)
    assert detect_board(damaged, layout) is None


def test_three_markers_are_enough(layout: dict, board_image: np.ndarray) -> None:
    damaged = board_image.copy()
    marker = next(m for m in layout["markers"] if m["id"] == 2)
    corners = np.asarray(marker["corners_px"], dtype=int)
    x0, y0 = corners.min(axis=0) - 8
    x1, y1 = corners.max(axis=0) + 8
    cv2.rectangle(damaged, (int(x0), int(y0)), (int(x1), int(y1)), (255, 255, 255), -1)
    pose = detect_board(damaged, layout)
    assert pose is not None
    assert pose.ids == [0, 1, 3]
    assert pose.is_valid and pose.reprojection_error < 5.0


@pytest.mark.parametrize("kind", ["noise", "white", "black"])
def test_blank_frame_returns_none(kind: str) -> None:
    rng = np.random.default_rng(11)
    if kind == "noise":
        frame = rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    elif kind == "white":
        frame = np.full((720, 1280, 3), 255, np.uint8)
    else:
        frame = np.zeros((720, 1280, 3), np.uint8)
    assert detect_board(frame) is None
    assert detect_board(np.zeros((0, 0, 3), np.uint8)) is None


def test_projection_visibility_and_render() -> None:
    pose = BoardPose(homography=np.eye(3), marker_ids=[0], reprojection_error=0.0,
                     coverage=0.5, is_valid=True, frame_size=(100, 100))
    layout = {
        "components": [
            {"component_id": "motor", "label": "Электродвигатель",
             "polygon_px": [[10, 10], [40, 10], [40, 40], [10, 40]]},
            {"component_id": "pump", "label": "Насос",
             "polygon_px": [[80, 80], [140, 80], [140, 140], [80, 140]]},
        ]
    }
    projected = project_components(pose, layout)
    assert projected[0]["visible"] is True
    assert projected[0]["in_frame_ratio"] == 1.0
    assert projected[1]["visible"] is False
    assert projected[1]["in_frame_ratio"] == pytest.approx(0.25)
    assert projected[0]["label"] == "Электродвигатель"

    frame = np.full((100, 100, 3), 30, np.uint8)
    original = frame.copy()
    canvas = render_overlay(frame, projected, pose)
    assert canvas.shape == frame.shape
    assert np.array_equal(frame, original), "render_overlay не должен менять входной кадр"
    assert canvas.sum() > frame.sum()
    assert project_components(None, layout) == []


def test_smoother_ema_and_reset() -> None:
    smoother = Smoother(alpha=0.5, reset_after=1.0)

    def zone(x: float) -> list[dict]:
        return [{"component_id": "motor", "label": "M", "visible": True,
                 "polygon": [[x, 0], [x + 10, 0], [x + 10, 10], [x, 10]]}]

    first = smoother.update(zone(0.0), timestamp=0.0)
    assert first[0]["polygon"][0] == [0.0, 0.0]

    second = smoother.update(zone(10.0), timestamp=0.1)
    assert second[0]["polygon"][0] == pytest.approx([5.0, 0.0])

    assert smoother.update([], timestamp=0.5) == []
    third = smoother.update(zone(20.0), timestamp=0.8)
    assert third[0]["polygon"][0] == pytest.approx([12.5, 0.0])

    smoother.update([], timestamp=2.5)
    fourth = smoother.update(zone(40.0), timestamp=2.6)
    assert fourth[0]["polygon"][0] == [40.0, 0.0], "после 1 c без маркеров — сброс"

    with pytest.raises(ValueError):
        Smoother(alpha=0.0)


def test_annotations_roundtrip(layout: dict, tmp_path: Path) -> None:
    path = tmp_path / "layout.json"
    save_layout(copy.deepcopy(layout), path)
    boxes = {"motor": (100.0, 200.0, 300.0, 400.0)}
    annotate.save_annotations(path, load_layout(path), boxes)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["components"]) == 1
    comp = data["components"][0]
    assert comp["component_id"] == "motor"
    assert comp["polygon_px"][0] == [100.0, 200.0]
    assert comp["polygon_norm"][2] == pytest.approx(
        [300.0 / data["board"]["width_px"], 400.0 / data["board"]["height_px"]], abs=1e-6)


def test_annotate_headless_message(monkeypatch, board_dir: Path, tmp_path: Path) -> None:
    # заодно проверяем не-ASCII путь (Windows + кириллица)
    folder = tmp_path / "Кириллица"
    folder.mkdir()
    shutil.copyfile(board_dir / "board_A3.png", folder / "board_A3.png")
    shutil.copyfile(board_dir / "layout.json", folder / "layout.json")

    def no_gui(*_args, **_kwargs):
        raise cv2.error("GUI is not available")

    monkeypatch.setattr(annotate.cv2, "namedWindow", no_gui)
    assert annotate.main(["--layout", str(folder / "layout.json")]) == 3


def test_unicode_path_io(tmp_path: Path) -> None:
    folder = tmp_path / "Кириллица"
    folder.mkdir()
    path = folder / "кадр.png"
    image = np.zeros((32, 48, 3), np.uint8)
    image[:, :] = (10, 200, 30)
    assert imwrite(path, image)
    restored = imread(path)
    assert restored is not None and restored.shape == image.shape
    assert int(restored[..., 1].mean()) == 200


def test_cli_help_without_camera(capsys) -> None:
    from ar_board import cli

    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--layout" in out and "--video" in out


def test_cli_video_file_end_to_end(layout: dict, board_image: np.ndarray,
                                   board_dir: Path, tmp_path: Path) -> None:
    from ar_board import cli

    rng = np.random.default_rng(42)
    video = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (960, 720))
    assert writer.isOpened(), "не удалось открыть VideoWriter"
    for _ in range(4):
        case = make_case(layout, board_image, (960, 720), rng, tilt_deg=15.0,
                         scale_range=(0.7, 0.85))
        writer.write(case["frame"])
    writer.release()

    code = cli.main(["--layout", str(board_dir / "layout.json"), "--video", str(video),
                     "--no-display", "--max-frames", "3"])
    assert code == 0
