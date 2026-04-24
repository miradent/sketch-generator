import tkinter as tk
from tkinter import ttk


class CodeSearcherUI:
    def __init__(self, root):
        self.root = root
        self.root.title("main.py 代码搜索器")
        self.root.geometry("800x500")

        self.file_path = "main.py"
        self.lines = []

        # ===== 输入框 =====
        top = ttk.Frame(root)
        top.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(top, text="搜索:").pack(side=tk.LEFT)

        self.entry = ttk.Entry(top)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        self.entry.bind("<KeyRelease>", self.search)

        # ===== 结果区 =====
        self.listbox = tk.Listbox(root, font=("Consolas", 10))
        self.listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.load_file()

    def load_file(self):
        with open(self.file_path, "r", encoding="utf-8") as f:
            self.lines = f.readlines()

    def search(self, event=None):
        keyword = self.entry.get().strip()

        self.listbox.delete(0, tk.END)

        if not keyword:
            return

        for i, line in enumerate(self.lines, 1):
            if keyword in line:
                self.listbox.insert(tk.END, f"{i}: {line.strip()}")


if __name__ == "__main__":
    root = tk.Tk()
    app = CodeSearcherUI(root)
    root.mainloop()