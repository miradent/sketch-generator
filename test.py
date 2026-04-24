import cnocr
import os
print(os.path.expanduser('~/.cnocr'))   # 通常是 C:\Users\86180\.cnocr
print(os.path.expanduser('~/.cnstd'))   # 通常是 C:\Users\86180\.cnstd



pyinstaller --windowed ^
  --add-data "models;models" ^
  --add-data "rapidocr;rapidocr" ^
  --add-data "copy_utils.py;." ^
  --add-data "pipfix.py;." ^
  --add-data "test.py;." ^
  --collect-all cnocr ^
  --collect-all cnstd ^
  --collect-all rapidocr ^
  --collect-all onnxruntime ^
  --collect-all shapely ^
  --collect-all scipy ^
  --collect-all torch ^
  --collect-all torchvision ^
  --collect-all numpy ^
  --collect-all PIL ^
  --collect-all sounddevice ^
  --collect-all websocket ^
  main.py