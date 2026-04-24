import os
import cv2
import ssl
import glob
import time
import hmac
import json
import base64
import hashlib
import threading
import numpy as np
import tkinter as tk
from PIL import Image, ImageTk
from urllib.parse import urlencode
from datetime import datetime, timezone
from tkinter import filedialog, messagebox, ttk

# 可选依赖导入

try:

    import sounddevice as sd
    SD_AVAILABLE = True

except ImportError:

    sd = None
    SD_AVAILABLE = False

try:

    import websocket
    WS_AVAILABLE = True

except ImportError:

    websocket = None
    WS_AVAILABLE = False

try:

    from cnocr import CnOcr
    OCR_AVAILABLE = True

except ImportError:

    CnOcr = None
    OCR_AVAILABLE = False

# 讯飞听写
class XunfeiSpeechRecognizer:

    def __init__(self, appid, api_key, api_secret):
        self.appid = appid
        self.api_key = api_key
        self.api_secret = api_secret
        self.host_url = "wss://iat-api.xfyun.cn/v2/iat"
        self.ws = None
        self.result_text = ""
        self.is_complete = False
        self.error_message = None
        self.recording = False
        self.audio_data = bytearray()

    def create_url(self):
        # 1.RFC1123时间戳(UTC+0)
        now = datetime.now(timezone.utc)
        # 注意：'%a, %d %b %Y %H:%M:%S GMT'
        date_str = now.strftime('%a, %d %b %Y %H:%M:%S GMT')
        # 2.签名原始字符(signature_origin)
        signature_origin = (
            f"host: iat-api.xfyun.cn\n"
            f"date: {date_str}\n"
            f"GET /v2/iat HTTP/1.1"
        )

        # 3.HMAC-SHA256 计算签名，Base64编码
        signature_sha = hmac.new(
            self.api_secret.encode('utf-8'),
            signature_origin.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()

        signature = base64.b64encode(signature_sha).decode('utf-8')
        # 4.authorization_origin字符
        authorization_origin = (
            f'api_key="{self.api_key}", algorithm="hmac-sha256", '
            f'headers="host date request-line", signature="{signature}"'
        )

        # 5.authorization_origin Base64编码
        authorization = base64.b64encode(authorization_origin.encode('utf-8')).decode('utf-8')
        # 6.参数字典 (date RFC1123)
        params = {
            "authorization": authorization,
            "date": date_str,
            "host": "iat-api.xfyun.cn"
        }

        # 7.拼接返回WebSocket URL
        return self.host_url + '?' + urlencode(params)

    def on_message(self, ws, message):

        try:

            data = json.loads(message)

            if data.get("code", 0) != 0:
                self.error_message = data.get("message", "未知错误")
                return

            if "data" in data:
                result = data["data"].get("result", {})
                ws_list = result.get("ws", [])

                if not hasattr(self, "result_dict"):
                    self.result_dict = {}

                sn = result.get("sn", 0)
                pgs = result.get("pgs")  # 关键字
                text_piece = ""

                for ws_item in ws_list:

                    for cw in ws_item.get("cw", []):
                        text_piece += cw.get("w", "")

                # 动态修正
                if pgs == "rpl":
                    # 覆盖
                    self.result_dict[sn] = text_piece
                else:
                    self.result_dict[sn] = text_piece

                # sn排序
                ordered = [self.result_dict[k] for k in sorted(self.result_dict)]
                full_text = "".join(ordered)
                # 去抖动
                import re
                full_text = re.sub(r'(.)\1+', r'\1', full_text)
                self.result_text = full_text

                if data["data"].get("status") == 2:
                    self.is_complete = True
                    ws.close()

        except Exception as e:

            self.error_message = str(e)

    def on_error(self, ws, error):

        self.error_message = str(error)

    def on_close(self, ws, close_status_code, close_msg):
        pass

    def on_open(self, ws):

        """握手,发开始帧"""
        first_frame = \
            {
                "common":
                    {
                        "app_id": self.appid
                    },
                "business":
                    {
                        "language": "zh_cn",
                        "domain": "iat",
                        "accent": "mandarin",
                        "dwa": "wpgs"
                    },

                "data":
                    {
                        "status": 0,
                        "format": "audio/L16;rate=16000",
                        "encoding": "raw",
                        "audio": ""
                    }
            }
        ws.send(json.dumps(first_frame))
        threading.Thread(target=self.send_audio_data, args=(ws,), daemon=True).start()

    def send_audio_data(self, ws):

        """发中间帧,结束帧"""
        chunk_size = 1280  # 40ms 音频数据量 (16000*2*0.04)
        data_index = 0

        while self.recording and not self.is_complete and data_index < len(self.audio_data):
            chunk = self.audio_data[data_index:data_index + chunk_size]

            if len(chunk) == 0:
                break

            audio_base64 = base64.b64encode(chunk).decode('utf-8')
            data_frame = \
                {
                    "data":
                        {
                            "status": 1,
                            "format": "audio/L16;rate=16000",
                            "encoding": "raw",
                            "audio": audio_base64
                        }
                }
            ws.send(json.dumps(data_frame))
            data_index += chunk_size
            time.sleep(0.04)

        # 发送结束帧
        end_frame = \
            {
                "data":
                    {
                        "status": 2,
                        "format": "audio/L16;rate=16000",
                        "encoding": "raw",
                        "audio": ""
                    }
            }
        ws.send(json.dumps(end_frame))

    def record_audio(self, duration=5, sample_rate=16000):

        """sounddevice录音（16kHz,单声道,16bit）"""
        try:

            self.audio_data = bytearray()

            def callback(indata, frames, time_info, status):

                if self.recording:
                    audio_int16 = (indata * 32767).astype(np.int16)
                    self.audio_data.extend(audio_int16.tobytes())

            with sd.InputStream(
                    samplerate=sample_rate,
                    channels=1,
                    callback=callback,
                    dtype=np.float32,
                    blocksize=1280
            ):
                time.sleep(duration)

        except Exception as e:

            self.error_message = f"录音错误: {e}"

    def recognize(self, timeout=8):

        """执行一次"""
        self.result_text = ""
        self.is_complete = False
        self.error_message = None
        self.recording = True
        self.audio_data = bytearray()

        if not SD_AVAILABLE:
            self.error_message = "请安装 sounddevice 库：pip install sounddevice"
            return None

        # 启动录音线程
        record_thread = threading.Thread(target=self.record_audio, args=(timeout - 1,), daemon=True)
        record_thread.start()
        time.sleep(0.5)

        if self.error_message:
            self.recording = False
            return None

        # 启动WebSocket连接线程
        def ws_thread():

            try:

                ws_url = self.create_url()
                print(f"连接 URL: {ws_url}")

                self.ws = websocket.WebSocketApp(
                    ws_url,
                    on_message=self.on_message,
                    on_error=self.on_error,
                    on_close=self.on_close,
                    on_open=self.on_open
                )

                self.ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})

            except Exception as e:

                self.error_message = f"WebSocket错误: {e}"
                self.is_complete = True

        ws_thread_obj = threading.Thread(target=ws_thread, daemon=True)
        ws_thread_obj.start()

        # 等待识别
        start_time = time.time()

        while not self.is_complete and (time.time() - start_time) < timeout:

            if self.error_message:
                break
            time.sleep(0.1)

        self.recording = False
        record_thread.join(timeout=1)

        if self.error_message:
            print(f"识别错误: {self.error_message}")
            return None

        text = self.result_text.strip()

        if not text or text == ".":
            return None
        return text


# 素描生成器主界面
class SketchGenerator:

    def _safe_fps_update(self, f):

        try:

            if hasattr(self, "fps_label") and self.fps_label:

                self.fps_label.config(text=f"FPS: {f:.1f}")

        except tk.TclError:

            pass

    """13种算法+彩绘线条+搜索+语音&OCR"""

    def __init__(self, root):

        self.root = root
        self.root.title("数字图像自动生成素描画")
        self.root.geometry("1550x880")
        self.style = ttk.Style()
        self.style.theme_use('clam')

        # 图像变量
        self.original_image = None
        self.original_pil = None
        self.sketch_result = None
        self.all_images = []
        self.current_search_path = r"C:\Users\86180\OneDrive\图片\屏幕快照"
        self.original_photo = None
        self.sketch_photo = None

        # 摄像头变量
        self.cap = None
        self.camera_active = False
        self.camera_window = None
        self.camera_label = None
        self.camera_status = None
        self.fps_label = None

        # OCR控制
        self.ocr_done = False
        self.ocr_lock = threading.Lock()
        self.current_frame = None
        self.camera_photo = None
        self.update_lock = threading.Lock()
        self.camera_mode = "capture"

        self.ocr_reader = CnOcr(
            det_model_name='ch_PP-OCRv3_det',
            rec_model_name='densenet_lite_136-gru',
            det_model_root_dir=r"E:\BOK\论文\ai模型\cnstd",
            rec_model_root_dir=r"E:\BOK\论文\ai模型\cnocr"
        )

        self.ocr_running = False
        self.ocr_load_attempted = False
        self.frame_queue = None
        self.latest_frame = None
        self.ocr_scan_interval = 3
        self.last_ocr_time = 0
        self.ocr_detected_text = None

        # 语音识别
        self.speech_recognizer = None
        self.voice_active = False

        # 讯飞API
        self.xf_appid = "c74f9bd5"
        self.xf_api_key = "abf76e7e730722569d8cdecffc1870e5"
        self.xf_api_secret = "ZmYyYWQ3YTAwY2E5M2NmNjcyMThlNjFj"

        # 初始化语音
        self.init_speech()

        # 算法参数
        self.algorithm_type = tk.StringVar(value="gaussian")
        self.k_size = tk.IntVar(value=21)
        self.strength = tk.DoubleVar(value=1.0)
        self.line_width = tk.IntVar(value=1)
        self.detail = tk.DoubleVar(value=1.0)
        self.edge_low = tk.IntVar(value=50)
        self.edge_high = tk.IntVar(value=150)
        self.blur_amount = tk.IntVar(value=5)
        self.blur_sigma = tk.DoubleVar(value=1.0)
        self.texture = tk.IntVar(value=0)
        self.hatch_density = tk.IntVar(value=5)
        self.hatch_angle = tk.IntVar(value=45)
        self.threshold = tk.IntVar(value=127)
        self.adaptive_block = tk.IntVar(value=11)
        self.quantization = tk.IntVar(value=8)
        self.color_line_strength = tk.DoubleVar(value=1.0)
        self.contrast = tk.DoubleVar(value=1.1)
        self.brightness = tk.IntVar(value=0)
        self.gamma = tk.DoubleVar(value=1.0)
        self.sharpen = tk.DoubleVar(value=1.0)
        self.denoise = tk.IntVar(value=3)
        self.vignette = tk.DoubleVar(value=0.0)
        self.grain = tk.IntVar(value=0)
        self.pencil_texture = tk.IntVar(value=0)
        self.edge_glow = tk.DoubleVar(value=0.0)
        self.soft_focus = tk.IntVar(value=0)
        self.invert_output = tk.BooleanVar(value=False)
        self.color_line_mode = tk.BooleanVar(value=False)
        self.recursive_search = tk.BooleanVar(value=True)
        self.create_ui()

    def init_speech(self):

        """初始化讯飞语音识别"""
        if not WS_AVAILABLE:
            print("安装 websocket-client：pip install websocket-client")
            return

        if not self.xf_appid or not self.xf_api_key or not self.xf_api_secret:
            print("配置讯飞 APPID、APIKey、APISecret")
            return

        try:

            self.speech_recognizer = XunfeiSpeechRecognizer(
                self.xf_appid, self.xf_api_key, self.xf_api_secret
            )
            print("讯飞语音识别初始化成功")

        except Exception as e:

            print(f"语音初始化失败: {e}")

    def ensure_ocr_loaded(self):

        if self.ocr_reader is not None:
            return True

        if self.ocr_load_attempted:
            return False

        if not OCR_AVAILABLE:
            return False

        self.ocr_load_attempted = True

        try:

            self.ocr_reader = CnOcr(model_name='densenet_lite_136-gru')
            print("CnOCR模型加载成功")
            return True

        except Exception as e:

            print(f"CnOCR加载失败: {e}")
            return False

    # ==================== UI 创建 ====================
    def create_ui(self):

        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(toolbar, text="📁 打开图片", command=self.open_image).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="📂 打开文件夹", command=self.open_folder).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="📷 拍照", command=lambda: self.open_camera_window("capture")).pack(side=tk.LEFT,
                                                                                                    padx=2)
        ttk.Button(toolbar, text="🔍 OCR扫描", command=lambda: self.open_camera_window("ocr")).pack(side=tk.LEFT, padx=2)
        self.capture_btn = ttk.Button(toolbar, text="📸 执行", command=self.camera_action, state=tk.DISABLED)
        self.capture_btn.pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="💾 保存作品", command=self.save_result).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="✨ 生成", command=self.generate).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Label(toolbar, text="预设:").pack(side=tk.LEFT, padx=5)

        self.preset_combo = ttk.Combobox(
            toolbar,
            values=["细腻铅笔", "粗犷炭笔", "钢笔速写", "水墨风格", "高对比度",
                    "柔和素描", "复古纹理", "清晰线稿", "浓墨重彩", "淡雅素描"],
            width=12, state="readonly"
        )

        self.preset_combo.pack(side=tk.LEFT, padx=2)
        self.preset_combo.bind('<<ComboboxSelected>>', self.apply_preset)
        ttk.Button(toolbar, text="⚙️ API配置", command=self.open_api_config).pack(side=tk.LEFT, padx=2)
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        left_frame = ttk.Frame(main_paned, width=420)
        main_paned.add(left_frame, weight=1)
        self.create_control_panel(left_frame)
        center_frame = ttk.Frame(main_paned)
        main_paned.add(center_frame, weight=4)
        self.create_display_panel(center_frame)
        right_frame = ttk.Frame(main_paned, width=300)
        main_paned.add(right_frame, weight=1)
        self.create_right_panel(right_frame)
        self.status_label = ttk.Label(self.root, text="就绪", relief=tk.SUNKEN)
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)

    def open_api_config(self):

        config_win = tk.Toplevel(self.root)
        config_win.title("讯飞API配置")
        config_win.geometry("500x280")
        config_win.resizable(False, False)
        ttk.Label(config_win, text="语音听写API配置", font=('Arial', 12, 'bold')).pack(pady=10)
        frame = ttk.Frame(config_win, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="APPID:").grid(row=0, column=0, sticky=tk.W, pady=5)
        appid_var = tk.StringVar(value=self.xf_appid)
        ttk.Entry(frame, textvariable=appid_var, width=45).grid(row=0, column=1, pady=5)
        ttk.Label(frame, text="APIKey:").grid(row=1, column=0, sticky=tk.W, pady=5)
        apikey_var = tk.StringVar(value=self.xf_api_key)
        ttk.Entry(frame, textvariable=apikey_var, width=45).grid(row=1, column=1, pady=5)
        ttk.Label(frame, text="APISecret:").grid(row=2, column=0, sticky=tk.W, pady=5)
        secret_var = tk.StringVar(value=self.xf_api_secret)
        ttk.Entry(frame, textvariable=secret_var, width=45, show="*").grid(row=2, column=1, pady=5)

        ttk.Label(frame, text="获取密钥：控制台 → 语音听写（流式版） → 服务管理",
                  font=('Arial', 8), foreground='blue').grid(row=3, column=0, columnspan=2, pady=5)

        def save_config():
            self.xf_appid = appid_var.get().strip()
            self.xf_api_key = apikey_var.get().strip()
            self.xf_api_secret = secret_var.get().strip()
            self.init_speech()

            if self.speech_recognizer:
                self.voice_btn.config(state=tk.NORMAL)
                messagebox.showinfo("成功", "API配置成功")
            config_win.destroy()

        btn_frame = ttk.Frame(config_win)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="保存", command=save_config).pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="取消", command=config_win.destroy).pack(side=tk.LEFT, padx=10)

    def create_right_panel(self, parent):

        search_frame = ttk.LabelFrame(parent, text="🔍 搜索图片", padding=8)
        search_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(search_frame, text="搜索路径:").pack(anchor=tk.W)
        path_frame = ttk.Frame(search_frame)
        path_frame.pack(fill=tk.X, pady=2)
        self.search_path_var = tk.StringVar(value=self.current_search_path)
        ttk.Entry(path_frame, textvariable=self.search_path_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(path_frame, text="浏览", command=self.browse_search_path, width=6).pack(side=tk.RIGHT, padx=2)

        ttk.Checkbutton(search_frame, text="搜索子文件夹", variable=self.recursive_search,
                        command=self.refresh_search).pack(anchor=tk.W, pady=2)

        ttk.Label(search_frame, text="输入关键词:").pack(anchor=tk.W, pady=(5, 0))
        search_input_frame = ttk.Frame(search_frame)
        search_input_frame.pack(fill=tk.X, pady=5)
        self.search_entry = ttk.Entry(search_input_frame)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.search_entry.bind('<KeyRelease>', self.on_search_keyword)
        self.voice_btn = ttk.Button(search_input_frame, text="🎤", command=self.start_voice_input, width=4)
        self.voice_btn.pack(side=tk.RIGHT, padx=2)

        if not WS_AVAILABLE or self.speech_recognizer is None:
            self.voice_btn.config(state=tk.DISABLED, text="🎤")

        btn_frame = ttk.Frame(search_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(btn_frame, text="🔍 搜索", command=self.search_by_filename, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="🔄 刷新", command=self.refresh_search, width=10).pack(side=tk.LEFT, padx=2)
        self.search_result_label = ttk.Label(search_frame, text="", font=('Arial', 8), foreground='gray')
        self.search_result_label.pack(anchor=tk.W)
        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(parent, text="📋 图片列表", font=('Arial', 12, 'bold')).pack(pady=5)

        self.path_label = ttk.Label(parent, text=f"路径: {self.current_search_path}",
                                    font=('Arial', 8), foreground='gray')

        self.path_label.pack(pady=2)
        ttk.Label(parent, text="双击图片加载", font=('Arial', 8), foreground='gray').pack()
        list_frame = ttk.Frame(parent)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.file_listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set,
                                       bg='#fafafa', selectbackground='#0078d4',
                                       selectforeground='white', font=('Arial', 9))

        self.file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.file_listbox.yview)
        self.file_listbox.bind('<Double-Button-1>', self.on_file_double_click)

    def create_control_panel(self, parent):

        canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scrollable = ttk.Frame(canvas)
        scrollable.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # canvas的frame随窗口拉伸
        def _configure_interior(event):
            canvas.itemconfig("all", width=event.width)

        canvas.bind('<Configure>', _configure_interior)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", on_mousewheel)
        ttk.Label(scrollable, text="🎨 13种素描算法", font=('Arial', 12, 'bold')).pack(pady=10)

        # 算法2列布局
        algo_frame = ttk.LabelFrame(scrollable, text="算法选择", padding=8)
        algo_frame.pack(fill=tk.X, padx=10, pady=5)

        algorithms = \
            [
                ("高斯模糊法(经典素描)", "gaussian"),
                ("边缘检测法(线条画)", "edge"),
                ("自适应阈值法(漫画黑白)", "adaptive"),
                ("形态学法(蚀刻版画)", "morphology"),
                ("双边滤波法(边缘保持)", "bilateral"),
                ("细节增强法(超清素描)", "detail"),
                ("交叉阴影法(排线素描)", "hatch"),
                ("拉普拉斯法(线框素描)", "laplacian"),
                ("XDoG算法(风格化素描)", "xdog"),
                ("铅笔画算法(纹理模拟)", "pencil_sketch"),
                ("水彩边缘法(柔和线条)", "water_edge"),
                ("炭笔画算法(颗粒质感)", "charcoal"),
                ("钢笔画算法(密集排线)", "pen_drawing")
            ]

        # 2列布局
        for i, (text, val) in enumerate(algorithms):
            row = i // 2
            col = i % 2

            rb = ttk.Radiobutton(algo_frame, text=text, variable=self.algorithm_type,
                                 value=val, command=self.auto_preview)

            rb.grid(row=row, column=col, sticky=tk.W, padx=10, pady=2)

        # 两列均匀分布
        algo_frame.columnconfigure(0, weight=1)
        algo_frame.columnconfigure(1, weight=1)

        # 参数滑块填满宽度
        param_frame = ttk.LabelFrame(scrollable, text="⚙️ 基础参数", padding=8)
        param_frame.pack(fill=tk.X, padx=10, pady=5)
        self.create_slider(param_frame, "模糊程度", self.k_size, 3, 51)
        self.create_slider(param_frame, "强度", self.strength, 0.1, 3.0)
        self.create_slider(param_frame, "细节保留", self.detail, 0.1, 2.0)
        edge_frame = ttk.LabelFrame(scrollable, text="🔪 边缘控制", padding=8)
        edge_frame.pack(fill=tk.X, padx=10, pady=5)
        self.create_slider(edge_frame, "边缘灵敏度", self.edge_low, 0, 255)
        self.create_slider(edge_frame, "边缘强度", self.edge_high, 0, 255)
        post_frame = ttk.LabelFrame(scrollable, text="🔧 后处理", padding=8)
        post_frame.pack(fill=tk.X, padx=10, pady=5)
        self.create_slider(post_frame, "对比度", self.contrast, 0.0, 3.0)
        self.create_slider(post_frame, "亮度", self.brightness, -100, 100)
        switch_frame = ttk.LabelFrame(scrollable, text="🎛️ 开关选项", padding=8)
        switch_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Checkbutton(switch_frame, text="反转黑白", variable=self.invert_output,
                        command=self.auto_preview).pack(anchor=tk.W)

        ttk.Checkbutton(switch_frame, text="彩绘线条模式", variable=self.color_line_mode,
                        command=self.auto_preview).pack(anchor=tk.W)

        btn_frame = ttk.Frame(scrollable)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        ttk.Button(btn_frame, text="重置所有参数", command=self.reset_all_params).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="生成作品", command=self.generate).pack(side=tk.RIGHT, padx=5)

    def create_slider(self, parent, label, var, from_val, to_val):

        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, pady=3)
        ttk.Label(frame, text=f"{label}:").pack(anchor=tk.W)

        # 水平布局滑块容器
        slider_frame = ttk.Frame(frame)
        slider_frame.pack(fill=tk.X)

        scale = ttk.Scale(slider_frame, from_=from_val, to=to_val, variable=var,
                          command=lambda x: self.auto_preview())

        scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
        val_lbl = ttk.Label(slider_frame, width=6)
        val_lbl.pack(side=tk.RIGHT, padx=5)

        def update_label(*args):

            val = var.get()

            if isinstance(val, float):

                val_lbl.config(text=f"{val:.1f}")
            else:
                val_lbl.config(text=str(val))

        var.trace('w', update_label)
        update_label()

    def create_display_panel(self, parent):

        left_frame = ttk.Frame(parent)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        right_frame = ttk.Frame(parent)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)
        ttk.Label(left_frame, text="原始图像", font=('Arial', 11, 'bold')).pack(pady=5)
        self.original_canvas = tk.Canvas(left_frame, bg='#f0f0f0', highlightthickness=1, highlightbackground='#ccc')
        self.original_canvas.pack(fill=tk.BOTH, expand=True)
        self.original_canvas.bind('<Configure>', self.on_canvas_resize)
        ttk.Label(right_frame, text="生成结果", font=('Arial', 11, 'bold')).pack(pady=5)
        self.sketch_canvas = tk.Canvas(right_frame, bg='white', highlightthickness=1, highlightbackground='#ccc')
        self.sketch_canvas.pack(fill=tk.BOTH, expand=True)
        self.sketch_canvas.bind('<Configure>', self.on_canvas_resize)
        self.info_label = ttk.Label(parent, text="未加载图片", font=('Arial', 9))
        self.info_label.pack(side=tk.BOTTOM, pady=5)

    # 语音输入
    def start_voice_input(self):

        if self.speech_recognizer is None:
            messagebox.showinfo("提示", "先配置讯飞 API")
            self.open_api_config()
            return

        if self.voice_active:
            return

        self.voice_active = True
        self.voice_btn.config(text="⏺️", state=tk.DISABLED)
        self.status_label.config(text=" 说话...")

        def recognize_thread():

            try:

                text = self.speech_recognizer.recognize(timeout=8)

                if text:

                    self.root.after(0, lambda t=text: self.handle_voice_result(t))
                elif self.speech_recognizer.error_message:
                    self.root.after(0, lambda: self.status_label.config(
                        text=f" {self.speech_recognizer.error_message}"))
                else:
                    self.root.after(0, lambda: self.status_label.config(text="未识别到语音"))

            except Exception as e:

                self.root.after(0, lambda: self.status_label.config(text=f"错误: {e}"))
            finally:

                self.voice_active = False
                self.root.after(0, lambda: self.voice_btn.config(text="", state=tk.NORMAL))

        threading.Thread(target=recognize_thread, daemon=True).start()

    def handle_voice_result(self, text):

        print(f"原始识别结果: [{text}]")  # 调试用

        if not text or text == ".":
            self.status_label.config(text="未识别到有效文字")
            return

        self.search_entry.delete(0, tk.END)
        self.search_entry.insert(0, text)
        self.status_label.config(text=f"识别: {text}")
        self.search_by_filename()

    def browse_search_path(self):

        path = filedialog.askdirectory(title="选择搜索文件夹")

        if path:
            norm_path = os.path.normpath(path)
            self.search_path_var.set(norm_path)
            self.current_search_path = norm_path
            self.path_label.config(text=f"路径: {norm_path}")
            self.refresh_search()

    def get_all_images(self, path, recursive=True):

        if not os.path.exists(path):
            return []

        exts = ['*.jpg', '*.jpeg', '*.png', '*.bmp', '*.tiff', '*.gif', '*.webp']
        images = []

        for ext in exts:

            try:

                if recursive:

                    images.extend(glob.glob(os.path.join(path, '**', ext), recursive=True))
                    images.extend(glob.glob(os.path.join(path, '**', ext.upper()), recursive=True))
                else:
                    images.extend(glob.glob(os.path.join(path, ext)))
                    images.extend(glob.glob(os.path.join(path, ext.upper())))

            except:

                pass

        return list(set(images))

    def search_by_filename(self):

        keyword = self.search_entry.get().strip().lower()
        search_path = os.path.normpath(self.search_path_var.get().strip())

        if not os.path.exists(search_path):
            messagebox.showerror("错误", f"路径不存在: {search_path}")
            return

        self.status_label.config(text=f"🔍 搜索: {keyword or '所有图片'}...")

        def search_thread():
            all_images = self.get_all_images(search_path, self.recursive_search.get())

            filtered = [img for img in all_images if
                        keyword in os.path.basename(img).lower()] if keyword else all_images

            self.root.after(0, lambda: self.update_search_results(filtered, keyword))

        threading.Thread(target=search_thread, daemon=True).start()

    def on_search_keyword(self, event):

        if hasattr(self, '_search_after_id'):
            self.root.after_cancel(self._search_after_id)

        self._search_after_id = self.root.after(300, self.search_by_filename)

    def refresh_search(self):

        self.search_by_filename()

    def update_search_results(self, results, keyword):

        self.file_listbox.delete(0, tk.END)
        self.all_images = results

        for img in results:
            self.file_listbox.insert(tk.END, os.path.basename(img))

        count = len(results)
        self.search_result_label.config(text=f"🔍 找到 {count} 张" if keyword else f"📷 共 {count} 张")
        self.status_label.config(text=f"✅ 找到 {count} 张")

    # 摄像头
    def open_camera_window(self, mode="capture"):

        if self.camera_active:
            return

        self.camera_mode = mode
        self.camera_window = tk.Toplevel(self.root)
        title = "拍照" if mode == "capture" else "OCR扫描 (每3秒检测)"
        self.camera_window.title(title)
        self.camera_window.geometry("660x560")
        self.camera_window.resizable(False, False)
        self.camera_window.protocol("WM_DELETE_WINDOW", self.close_camera)
        self.camera_window.attributes('-topmost', True)
        info_text = "按「执行」拍照" if mode == "capture" else "OCR扫描中，检测到文字自动关闭"
        video_frame = ttk.LabelFrame(self.camera_window, text=info_text, padding=5)
        video_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        video_container = ttk.Frame(video_frame, width=640, height=480)
        video_container.pack_propagate(False)
        video_container.pack()
        self.camera_label = ttk.Label(video_container, background='#2b2b2b')
        self.camera_label.pack(fill=tk.BOTH, expand=True)
        info_frame = ttk.Frame(self.camera_window)
        info_frame.pack(fill=tk.X, padx=10, pady=5)
        self.camera_status = ttk.Label(info_frame, text="正在启动...")
        self.camera_status.pack(side=tk.LEFT)
        self.fps_label = ttk.Label(info_frame, text="FPS: --")
        self.fps_label.pack(side=tk.RIGHT)

        if mode == "capture":

            self.capture_btn.config(text="拍照", state=tk.NORMAL)
        else:
            self.capture_btn.config(state=tk.DISABLED)
            self.ensure_ocr_loaded()

        self.last_ocr_time = time.time()
        self.start_camera()

    def start_camera(self):

        for index in [0, 1]:

            self.cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)

            if self.cap.isOpened():
                break

        if not self.cap or not self.cap.isOpened():
            messagebox.showerror("错误", "无法打开摄像头")
            self.close_camera()
            return

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.camera_active = True
        self.camera_status.config(text="摄像头就绪")
        threading.Thread(target=self.update_camera_feed, daemon=True).start()
        self.root.after(30, self.render_frame)

    def update_camera_feed(self):

        frame_count = 0
        fps_timer = time.time()

        while self.camera_active and self.cap and self.cap.isOpened():

            ret, frame = self.cap.read()

            if not ret:
                continue

            with self.update_lock:

                self.current_frame = frame.copy()
                frame_count += 1
                now = time.time()

                if now - fps_timer >= 1.0:

                    fps = frame_count / (now - fps_timer)

                    if self.camera_active and self.fps_label:

                        self.root.after(0, lambda f=fps: self._safe_fps_update(f))
                    frame_count = 0

                    fps_timer = now

                    def _safe_fps_update(self, f):

                        if self.fps_label and str(self.fps_label) != "None":

                            try:

                                self.fps_label.config(text=f"FPS: {f:.1f}")

                            except tk.TclError:

                                pass

                if self.camera_mode == "ocr":

                    now = time.time()

                    if (not self.ocr_running) and (not self.ocr_done) and (
                            now - self.last_ocr_time >= self.ocr_scan_interval):
                        self.last_ocr_time = now
                        self.ocr_running = True
                        frame_copy = self.current_frame.copy()

                        threading.Thread(
                            target=self.ocr_detect,
                            args=(frame_copy,),
                            daemon=True
                        ).start()

                remain = max(0, self.ocr_scan_interval - int(now - self.last_ocr_time))
                status_text = "Press 'Capture'" if self.camera_mode == "capture" else f"OCR {remain}s"

                cv2.putText(self.current_frame, status_text, (10, 25),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

                rgb = cv2.cvtColor(self.current_frame, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(rgb)
                pil_img.thumbnail((640, 480), Image.Resampling.LANCZOS)
                final = Image.new('RGB', (640, 480), '#2b2b2b')
                w, h = pil_img.size
                final.paste(pil_img, ((640 - w) // 2, (480 - h) // 2))
                self.latest_frame = final

                if self.camera_active and self.camera_label:
                    self.frame_queue = self.latest_frame
                    self.root.after(0, self.safe_update_display)

            time.sleep(0.03)

    def safe_update_display(self):

        if self.camera_active and self.camera_label and self.frame_queue:

            try:

                photo = ImageTk.PhotoImage(self.frame_queue)
                self.camera_photo = photo
                self.camera_label.config(image=photo)
            except:

                pass

    def render_frame(self):

        if hasattr(self, "latest_frame") and self.latest_frame is not None:

            try:

                photo = ImageTk.PhotoImage(self.latest_frame)
                self.camera_photo = photo

                if self.camera_label:
                    self.camera_label.config(image=photo)

            except:

                pass

        if self.camera_active:
            self.root.after(30, self.render_frame)

    def ocr_detect(self, frame):

        try:

            print("OCR触发")

            if not self.ensure_ocr_loaded():
                return

            results = self.ocr_reader.ocr(frame)
            text = ""

            for item in results:

                if isinstance(item, dict):
                    text += item.get("text", "")
                elif isinstance(item, (list, tuple)):
                    text += str(item[0])
                else:
                    text += str(item)

            text = text.strip()
            print("OCR结果:", text)

            if text:

                with self.ocr_lock:

                    if self.ocr_done:
                        return

                    self.ocr_done = True

                def finish():

                    self.search_entry.delete(0, tk.END)
                    self.search_entry.insert(0, text)
                    self.status_label.config(text=f"OCR: {text}")
                    self.close_camera()
                    self.ocr_done = False
                    self.ocr_running = False
                    self.last_ocr_time = 0

                self.root.after(0, finish)

        except Exception as e:

            print("OCR错误:", e)

        finally:

            self.ocr_running = False

    def handle_ocr_result(self, text):

        self.search_entry.delete(0, tk.END)
        self.search_entry.insert(0, text)
        self.status_label.config(text=f"OCR识别: {text}")
        self.search_by_filename()

    def camera_action(self):

        if self.camera_mode == "capture":
            self.capture_photo()

    def capture_photo(self):

        if not self.camera_active or self.current_frame is None:
            return

        with self.update_lock:
            frame = self.current_frame.copy()

        self.original_image = frame
        self.original_pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        self.show_pil_on_canvas(self.original_pil, self.original_canvas)
        self.info_label.config(text=f"📷 拍摄 {self.original_pil.size[0]}x{self.original_pil.size[1]}")
        self.status_label.config(text="📸 拍摄成功")
        self.auto_preview()

    def close_camera(self):

        self.camera_active = False

        if self.cap:
            self.cap.release()
            self.cap = None

        if self.camera_window:
            self.camera_window.destroy()
            self.camera_window = None

        self.capture_btn.config(state=tk.DISABLED, text="执行")

    # 图像显示处理
    def on_canvas_resize(self, event):

        if event.widget == self.original_canvas and self.original_pil:

            self.show_pil_on_canvas(self.original_pil, self.original_canvas)
        elif event.widget == self.sketch_canvas and self.sketch_result is not None:
            self.show_result_on_canvas()

    def show_pil_on_canvas(self, pil_img, canvas):

        if pil_img is None:
            return
        w, h = canvas.winfo_width(), canvas.winfo_height()

        if w <= 5 or h <= 5:
            w, h = 400, 400

        img_w, img_h = pil_img.size
        scale = min(w / img_w, h / img_h) * 0.95
        new_w, new_h = int(img_w * scale), int(img_h * scale)
        resized = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(resized)
        canvas.delete("all")
        canvas.create_image(w // 2, h // 2, image=photo, anchor=tk.CENTER)

        if canvas == self.original_canvas:

            self.original_photo = photo
        else:
            self.sketch_photo = photo

    def show_result_on_canvas(self):

        if self.sketch_result is not None:

            if len(self.sketch_result.shape) == 3:

                pil_img = Image.fromarray(cv2.cvtColor(self.sketch_result, cv2.COLOR_BGR2RGB))
            else:
                h, w = self.sketch_result.shape
                white_bg = np.ones((h, w, 3), dtype=np.uint8) * 255

                for c in range(3):
                    white_bg[:, :, c] = self.sketch_result

                pil_img = Image.fromarray(white_bg)

            self.show_pil_on_canvas(pil_img, self.sketch_canvas)

    def open_image(self):

        path = filedialog.askopenfilename(
            title="选择图片",
            filetypes=[("图像文件", "*.jpg *.jpeg *.png *.bmp *.tiff"), ("所有文件", "*.*")]
        )

        if path:
            self.load_image(path)

    def open_folder(self):

        path = filedialog.askdirectory(title="选择图片文件夹")

        if path:
            norm_path = os.path.normpath(path)
            self.current_search_path = norm_path
            self.search_path_var.set(norm_path)
            self.path_label.config(text=f"路径: {norm_path}")
            self.refresh_search()

    def on_file_double_click(self, event):

        selection = self.file_listbox.curselection()

        if selection:

            idx = selection[0]
            displayed_name = self.file_listbox.get(idx)
            for img in self.all_images:

                if os.path.basename(img) == displayed_name:
                    self.load_image(img)
                    break

    def load_image(self, path):

        try:

            self.original_pil = Image.open(path)
            self.original_image = cv2.cvtColor(np.array(self.original_pil), cv2.COLOR_RGB2BGR)
            self.show_pil_on_canvas(self.original_pil, self.original_canvas)
            self.info_label.config(text=f"{os.path.basename(path)}")
            self.status_label.config(text="已加载")
            self.auto_preview()

        except Exception as e:

            messagebox.showerror("错误", f"加载失败: {e}")

    def auto_preview(self):

        if self.original_image is not None:
            self.generate()

    def generate(self):

        if self.original_image is None:
            messagebox.showinfo("提示", "请先选择图片")
            return

        def process():

            try:

                result = self.create_artwork(self.original_image)
                self.sketch_result = result
                self.root.after(0, self.show_result_on_canvas)
                self.root.after(0, lambda: self.status_label.config(text="生成完成"))

            except Exception as e:

                self.root.after(0, lambda: self.status_label.config(text=f"错误: {e}"))

        self.status_label.config(text="正在生成")
        threading.Thread(target=process, daemon=True).start()

    # 素描算法
    def create_artwork(self, img):

        sketch = self.apply_sketch_algorithm(img, self.algorithm_type.get())
        sketch = self.apply_post_processing(sketch)

        if self.color_line_mode.get():
            sketch = self.apply_color_to_lines(img, sketch)

        return self.ensure_white_background(sketch)

    def apply_color_to_lines(self, original_img, sketch):

        if len(sketch.shape) == 2:

            sketch_bgr = cv2.cvtColor(sketch, cv2.COLOR_GRAY2BGR)
        else:
            sketch_bgr = sketch.copy()

        gray_sketch = cv2.cvtColor(sketch_bgr, cv2.COLOR_BGR2GRAY)
        line_weight = (255 - gray_sketch).astype(np.float32) / 255.0
        line_weight = np.power(line_weight, 1.5) * self.color_line_strength.get()
        line_weight = np.clip(line_weight, 0, 1)
        hsv = cv2.cvtColor(original_img, cv2.COLOR_BGR2HSV)
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * (0.8 + 0.4 * self.color_line_strength.get()), 0, 255).astype(np.uint8)
        enhanced_img = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        result = np.ones_like(original_img, dtype=np.float32) * 255

        for c in range(3):
            result[:, :, c] = (1 - line_weight) * 255 + line_weight * enhanced_img[:, :, c]

        return np.clip(result, 0, 255).astype(np.uint8)

    def apply_sketch_algorithm(self, img, algo):

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img.copy()

        if algo == "gaussian":

            k = self.k_size.get()

            if k % 2 == 0: k += 1
            inv = 255 - gray
            blur = cv2.GaussianBlur(inv, (k, k), self.blur_sigma.get())
            denom = 255 - blur.astype(np.float32) + 1e-5
            result = gray.astype(np.float32) * self.strength.get() * 255 / denom
            return np.clip(result, 0, 255).astype(np.uint8)
        elif algo == "edge":
            b = self.blur_amount.get()

            if b % 2 == 0: b += 1
            blur = cv2.GaussianBlur(gray, (b, b), 0)
            edges = cv2.Canny(blur, self.edge_low.get(), self.edge_high.get())
            return 255 - edges
        elif algo == "adaptive":
            b = self.adaptive_block.get()

            if b % 2 == 0: b += 1
            return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, b, 2)
        elif algo == "morphology":
            b = self.blur_amount.get()

            if b % 2 == 0: b += 1
            blur = cv2.GaussianBlur(gray, (b, b), 0)
            edges = cv2.Canny(blur, self.edge_low.get(), self.edge_high.get())
            kernel = np.ones((self.line_width.get(), self.line_width.get()), np.uint8)
            return cv2.morphologyEx(255 - edges, cv2.MORPH_CLOSE, kernel)
        elif algo == "bilateral":
            d = self.k_size.get()

            if d % 2 == 0: d += 1
            bilateral = cv2.bilateralFilter(gray, d, self.edge_high.get(), self.edge_low.get())
            inv = 255 - bilateral
            blur = cv2.GaussianBlur(inv, (d, d), 0)
            denom = 255 - blur.astype(np.float32) + 1e-5
            result = gray.astype(np.float32) * 255 / denom
            return np.clip(result * self.strength.get(), 0, 255).astype(np.uint8)
        elif algo == "detail":

            k = self.k_size.get()

            if k % 2 == 0: k += 1
            inv = 255 - gray
            blur1 = cv2.GaussianBlur(inv, (k, k), 0)
            blur2 = cv2.GaussianBlur(inv, (k * 2 + 1, k * 2 + 1), 0)
            detail = cv2.addWeighted(blur1, 1.5, blur2, -0.5, 0)
            denom = 255 - detail.astype(np.float32) + 1e-5
            result = gray.astype(np.float32) * self.detail.get() * 255 / denom
            return np.clip(result, 0, 255).astype(np.uint8)
        elif algo == "hatch":

            sobelx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
            sobely = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
            mag = np.sqrt(sobelx ** 2 + sobely ** 2)
            mag = (mag / (mag.max() + 1e-5) * 255).astype(np.uint8)
            h, w = gray.shape
            result = np.ones((h, w), dtype=np.uint8) * 255
            density = max(2, 20 - self.hatch_density.get())

            for i in range(0, h, density):

                for j in range(0, w, density):

                    if mag[i, j] > self.edge_low.get():

                        angle = np.radians(self.hatch_angle.get())
                        cv2.line(result, (j, i), (int(j + 8 * np.cos(angle)), int(i + 8 * np.sin(angle))), 0, 1)

                        cv2.line(result, (j, i),
                                 (int(j + 8 * np.cos(angle + np.pi / 2)), int(i + 8 * np.sin(angle + np.pi / 2))), 0, 1)

            return result
        elif algo == "laplacian":

            b = self.blur_amount.get()

            if b % 2 == 0: b += 1
            blur = cv2.GaussianBlur(gray, (b, b), 0)
            lap = cv2.Laplacian(blur, cv2.CV_64F)
            lap = np.uint8(np.absolute(lap))
            _, result = cv2.threshold(lap, self.edge_low.get(), 255, cv2.THRESH_BINARY_INV)
            return result
        elif algo == "xdog":
            b = self.blur_amount.get()

            if b % 2 == 0: b += 1
            blur = cv2.GaussianBlur(gray, (b, b), self.blur_sigma.get())
            dog = gray.astype(np.float32) - self.detail.get() * blur.astype(np.float32)
            dog = (dog - dog.min()) / (dog.max() - dog.min() + 1e-5)
            result = cv2.threshold((dog * 255).astype(np.uint8), self.threshold.get(), 255, cv2.THRESH_BINARY)
            return 255 - result
        elif algo == "pencil_sketch":
            k = self.k_size.get()

            if k % 2 == 0: k += 1
            inv = 255 - gray
            blur = cv2.GaussianBlur(inv, (k, k), 0)
            result = cv2.divide(gray, 255 - blur, scale=256)
            return np.clip(result * self.strength.get(), 0, 255).astype(np.uint8)
        elif algo == "water_edge":
            d = self.k_size.get()

            if d % 2 == 0: d += 1
            bilateral = cv2.bilateralFilter(gray, d, self.edge_high.get(), self.edge_low.get())
            edges = cv2.Canny(bilateral, self.edge_low.get(), self.edge_high.get())
            edges = cv2.dilate(edges, None, iterations=1)
            edges = cv2.GaussianBlur(edges, (3, 3), 0)
            return 255 - edges
        elif algo == "charcoal":
            b = self.blur_amount.get()

            if b % 2 == 0: b += 1
            blur = cv2.GaussianBlur(gray, (b, b), 0)
            edges = cv2.Canny(blur, self.edge_low.get(), self.edge_high.get())
            result = 255 - edges
            noise = np.random.normal(0, self.texture.get(), gray.shape).astype(np.uint8)
            return cv2.addWeighted(result, 0.8, noise, 0.2, 0)
        elif algo == "pen_drawing":
            b = self.adaptive_block.get()

            if b % 2 == 0: b += 1
            binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, b, 2)
            kernel = np.ones((2, 2), np.uint8)
            result = cv2.erode(binary, kernel, iterations=1)
            return cv2.dilate(result, kernel, iterations=1)
        else:
            return gray

    def apply_post_processing(self, img):

        if len(img.shape) == 2:

            result = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        else:
            result = img.copy()

        result = cv2.convertScaleAbs(result, alpha=self.contrast.get(), beta=self.brightness.get())
        gamma = self.gamma.get()

        if gamma != 1.0:

            inv_gamma = 1.0 / gamma
            table = np.array([(i / 255.0) ** inv_gamma * 255 for i in range(256)]).astype(np.uint8)
            result = cv2.LUT(result, table)
        sharpen_val = self.sharpen.get()

        if sharpen_val > 1.0:

            kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]]) * (sharpen_val / 2)
            result = cv2.filter2D(result, -1, kernel)

        if self.denoise.get() > 0 and len(result.shape) == 3:

            result = cv2.fastNlMeansDenoisingColored(result, None, self.denoise.get(), self.denoise.get(), 7, 21)

        if self.invert_output.get():

            result = 255 - result

        return result

    def ensure_white_background(self, img):

        if len(img.shape) == 2:

            h, w = img.shape
            white_bg = np.ones((h, w, 3), dtype=np.uint8) * 255

            for c in range(3):

                white_bg[:, :, c] = img

            return white_bg

        return img

    def apply_preset(self, event):

        preset = event.widget.get()

        presets = {
            "细腻铅笔": lambda: [self.algorithm_type.set("gaussian"), self.k_size.set(5), self.strength.set(1.2)],
            "粗犷炭笔": lambda: [self.algorithm_type.set("charcoal"), self.k_size.set(31), self.strength.set(1.8)],
            "高对比度": lambda: [self.algorithm_type.set("xdog"), self.threshold.set(120), self.contrast.set(2.0)],
        }

        if preset in presets:

            presets[preset]()
            self.auto_preview()

    def reset_all_params(self):

        self.algorithm_type.set("gaussian")
        self.k_size.set(21)
        self.strength.set(1.0)
        self.edge_low.set(50)
        self.edge_high.set(150)
        self.blur_amount.set(5)
        self.threshold.set(127)
        self.contrast.set(1.1)
        self.invert_output.set(False)
        self.color_line_mode.set(False)
        self.auto_preview()

    def save_result(self):

        if self.sketch_result is None:

            messagebox.showwarning("提示", "没有可保存的结果")
            return

        path = filedialog.asksaveasfilename(
            title="保存作品",
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg")]
        )

        if path:

            try:

                if len(self.sketch_result.shape) == 3:

                    img = Image.fromarray(cv2.cvtColor(self.sketch_result, cv2.COLOR_BGR2RGB))
                else:
                    h, w = self.sketch_result.shape
                    white_bg = np.ones((h, w, 3), dtype=np.uint8) * 255

                    for c in range(3):

                        white_bg[:, :, c] = self.sketch_result
                    img = Image.fromarray(white_bg)
                img.save(path)

                messagebox.showinfo("成功", f"已保存")

            except Exception as e:
                messagebox.showerror("错误", f"保存失败: {e}")

    def __del__(self):

        try:

            self.camera_active = False

            if hasattr(self, 'cap') and self.cap is not None:

                self.cap.release()
            cv2.destroyAllWindows()

        except:

            pass


#程序入口
import ctypes

try:

    ctypes.windll.ole32.CoInitialize(None)

except:

    pass

def main():

    root = tk.Tk()
    app = SketchGenerator(root)
    root.mainloop()

if __name__ == "__main__":
    main()