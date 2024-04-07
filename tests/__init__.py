import logging
import os

import mailchimp_transactional
import wrapt

logger = logging.getLogger(__name__)


def get_asset_path(name):
    assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
    return os.path.join(assets, name)

#
# global mocks, patches, stubs
#


@wrapt.patch_function_wrapper(mailchimp_transactional.MessagesApi, 'send')
def patch_email_send(wrapped, instance, args, kwargs):
    """Patch out the Mandrill email sender."""
    message = args[0]['message']
    if not message:
        logger.info('patching successful empty email send')
    subj = message['subject']
    to = ', '.join([_['email'] for _ in message['to']])
    logger.warning(f"Simulated successful email '{subj}' to {to}")
