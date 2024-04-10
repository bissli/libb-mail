from mail.client import *
from mail import config as __config

globals().update(**__config.mail)
globals().update(**__config.mandrill)
