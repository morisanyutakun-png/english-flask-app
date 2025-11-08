import logging
logging.basicConfig(level=logging.INFO)

try:
    x = int("abc")
except Exception as e:
    logging.error("例外発生: %s", e, exc_info=True)
