"""テーマの背景画像 (設定の「テーマ」タブ)。

画像は大きいので config.json には入れず、data/theme_images/<image_id>.<拡張子> に置く。
UI は選ばれた画像を縮めてから data URL で送ってきて (/run/save_ui_theme_image)、
表示するときに data URL で受け取る (/run/load_ui_theme_image)。
どのテーマがどの画像を使っているかは config.UI_THEME にだけあるので、使われなくなった
画像は起動時に removeUnusedImages() で消す (保存の直後に消すと、画像を保存してから
テーマを書き換えるまでの間に届いた別の保存で消してしまうため)。
"""

import base64
import binascii
import os
import re

IMAGE_DIR_NAME = "theme_images"
MAX_IMAGE_BYTES = 8 * 1024 * 1024

_IMAGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DATA_URL_RE = re.compile(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/]+={0,2})$")
_MIME_TO_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_EXT_TO_MIME = {ext: mime for mime, ext in _MIME_TO_EXT.items()}


def isValidImageId(image_id) -> bool:
    return isinstance(image_id, str) and _IMAGE_ID_RE.match(image_id) is not None


def _matchesMime(data: bytes, mime: str) -> bool:
    # 拡張子だけを信じず、中身の先頭で種類を確かめる。
    if mime == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if mime == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def _pathsOf(image_dir: str, image_id: str) -> list:
    return [os.path.join(image_dir, f"{image_id}.{ext}") for ext in _EXT_TO_MIME]


def saveImage(image_dir: str, image_id: str, data_url: str) -> None:
    """data URL の画像を保存する。受け取れない値なら ValueError。"""
    if not isValidImageId(image_id):
        raise ValueError("invalid image id")
    if not isinstance(data_url, str):
        raise ValueError("data_url must be a string")
    match = _DATA_URL_RE.match(data_url)
    if match is None:
        raise ValueError("unsupported data URL")
    mime, encoded = match.groups()
    # base64 は 4 文字で 3 バイトなので、デコードする前に大きさを見積もって弾く。
    if len(encoded) * 3 // 4 > MAX_IMAGE_BYTES:
        raise ValueError("image is too large")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError("broken base64") from e
    if not _matchesMime(data, mime):
        raise ValueError("image data does not match its type")

    os.makedirs(image_dir, exist_ok=True)
    path = os.path.join(image_dir, f"{image_id}.{_MIME_TO_EXT[mime]}")
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "wb") as fp:
        fp.write(data)
    os.replace(tmp_path, path)
    # 同じ id で別の種類の画像が残っていたら消す (読み込みで古いほうを返さないように)。
    for other in _pathsOf(image_dir, image_id):
        if other != path and os.path.isfile(other):
            os.remove(other)


def loadImage(image_dir: str, image_id: str):
    """保存した画像を data URL で返す。無ければ None。"""
    if not isValidImageId(image_id):
        raise ValueError("invalid image id")
    for path in _pathsOf(image_dir, image_id):
        if os.path.isfile(path):
            with open(path, "rb") as fp:
                data = fp.read()
            mime = _EXT_TO_MIME[path.rsplit(".", 1)[1]]
            return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    return None


def usedImageIds(ui_theme) -> set:
    """config.UI_THEME の自分のテーマが背景に使っている画像の id。"""
    ids = set()
    if not isinstance(ui_theme, dict):
        return ids
    for theme in ui_theme.get("custom_themes") or []:
        if not isinstance(theme, dict):
            continue
        backdrop = theme.get("backdrop")
        image = backdrop.get("image") if isinstance(backdrop, dict) else None
        image_id = image.get("id") if isinstance(image, dict) else None
        if isValidImageId(image_id):
            ids.add(image_id)
    return ids


def removeUnusedImages(image_dir: str, used_ids: set) -> list:
    """どのテーマも使っていない画像を消し、消したファイル名を返す。"""
    removed = []
    if not os.path.isdir(image_dir):
        return removed
    for name in os.listdir(image_dir):
        path = os.path.join(image_dir, name)
        if not os.path.isfile(path):
            continue
        image_id, _, ext = name.rpartition(".")
        if ext in _EXT_TO_MIME and image_id in used_ids:
            continue
        os.remove(path)
        removed.append(name)
    return removed
