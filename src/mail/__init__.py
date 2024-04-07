import logging
import pathlib

import wrapt


@wrapt.patch_function_wrapper('mimetypes', 'init')
def patch_mimetypes_init(wrapped, instance, args, kwargs):
    """Patch init known files"""
    knownfiles = pathlib.Path(__file__).parent.absolute() / 'mime.types'
    return wrapped([str(knownfiles)])


from mail.mail import *
