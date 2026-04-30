import logging
import os
import pathlib

import mailchimp_transactional
import wrapt

logger = logging.getLogger(__name__)


def get_asset_path(name):
    assets = os.path.join(pathlib.Path(pathlib.Path(__file__).resolve()).parent, 'assets')
    return os.path.join(assets, name)

#
# global mocks, patches, stubs
#


@wrapt.patch_function_wrapper(mailchimp_transactional.MessagesApi, 'send')
def patch_email_send(wrapped, instance, args, kwargs):
    """Patch out the Mandrill email sender, returning a synthetic per-recipient
    response so _send_via_mandrill's post-check and result extraction succeed.
    """
    message = args[0]['message']
    if not message:
        logger.info('patching successful empty email send')
        return []
    subj = message['subject']
    recipients = message['to']
    to = ', '.join([r['email'] for r in recipients])
    logger.warning(f"Simulated successful email '{subj}' to {to}")
    return [
        {'_id': f'fake-{i}',
         'email': r['email'],
         'status': 'sent',
         'reject_reason': None}
        for i, r in enumerate(recipients)
        ]
