#!/usr/bin/env python3
# coding: utf-8
import argparse

from whaleyeah import serve_config

if __name__=="__main__":
    parser = argparse.ArgumentParser(
        prog="bot.py",
        description="Yet another telegram bot.",
        epilog="_(:з」∠)_",
    )
    parser.add_argument("-c", "--config", type=str, help="configuration json file", default="config.json")
    args = parser.parse_args()

    serve_config(args.config)