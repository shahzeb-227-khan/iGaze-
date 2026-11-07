import os
import sys
from pathlib import Path


def resource_path(name):
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent)) / name


def data_path(name):
    base = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share')) / 'iGAZE'
    return base / name
