REM .venv exists
if exist .venv (
    rmdir /s /q .venv
)

REM make .venv
python -m venv .venv

REM install packages for .venv
call .venv/Scripts/activate
python.exe -m pip install --upgrade pip
pip install --no-cache-dir --force-reinstall -r requirements.txt
REM rapidocr requires opencv-python, but this app uses opencv-python-headless.
REM Both provide the same cv2 and collide, so reinstall it without its
REM dependencies (the dependencies are listed explicitly in requirements).
pip install --no-cache-dir --force-reinstall --no-deps rapidocr==3.9.2
python -X utf8 tools\fetch_ocr_models.py
