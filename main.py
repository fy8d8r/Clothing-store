import tkinter as tk
from tkinter import messagebox

from store.db import Database, DatabaseError
from store.gui import App


def main():
    try:
        db = Database()
    except DatabaseError as e:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Не удалось подключиться к MySQL", str(e))
        return
    App(db).mainloop()


if __name__ == "__main__":
    main()