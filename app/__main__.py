"""`python -m app [serve|check|pool]` 的入口。"""
import sys

from app.cli import main

if __name__ == '__main__':
    sys.exit(main())
