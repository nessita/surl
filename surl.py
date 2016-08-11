#!/usr/bin/env python3
"""
S(tore)URL ....

Authorising the client::

  $ surl -e foo@bar.com -s production surl -a .auth

  OR

  $ STORE_EMAIL=foo@bar.com STORE_ENV=production surl -a .auth
  Password for foo@bar.com: *****
  2FA (if enabled):
  ...

That will record authorization information in `.auth` path.
(CAREFULL, IT WILL BE IN PLAINTEXT)

User '-p package_access package_upload' switch to create more capable
authorizations.

Then use it for performing actions on the Store API (defaults to
ACL verification)::

  $ ./surl.py -a .auth | jq .
  {
    "account": {
      "openid": "*****",
      "verified": true,
      "displayname": "Foo Bar",
      "email": "foo@bar.com"
    },
    "last_auth": "2016-08-11T01:23:02.627614",
    "refresh_required": false,
    "allowed": true,
    "permissions": [
      "package_access"
    ]
  }

Registering a new snap name::

  $ ./surl.py -a .auth -d'{"snap_name": "surl"}' \
    https://myapps.developer.ubuntu.com/dev/api/register-name/ | jq .
  {
    "snap_id": "LpV8761EjlAPqeXxfYhQvpSWgpxvEWpN"
  }

"""

import argparse
import getpass
import json
import logging
import os
import sys

import requests
from pymacaroons import Macaroon

logging.basicConfig(format='%(asctime)s %(levelname)-5.5s %(name)s %(message)s')
logger = logging.getLogger(__name__)


DEFAULT_HEADERS = {
    'Accept': 'application/json, application/hal+json',
    'Content-Type': 'application/json',
    'Cache-Control': 'no-cache',
}

CONSTANTS = {
    'staging': {
        'sso_location': 'login.staging.ubuntu.com',
        'sso_base_url': 'https://login.staging.ubuntu.com',
        'sca_base_url': 'https://myapps.developer.staging.ubuntu.com',
    },
    'production': {
        'sso_location': 'login.ubuntu.com',
        'sso_base_url': 'https://login.ubuntu.com',
        'sca_base_url': 'https://myapps.developer.ubuntu.com',
    },
}


def get_store_authorization(email, permissions=None, store_env=None):
    """Return the authorization header for Store requests.

    Get a permissions macaroon from SCA and discharge it in SSO.
    """
    headers = DEFAULT_HEADERS.copy()
    sca_data = {
        'permissions': permissions or ['package_access'],
    }
    response = requests.request(
        url='{}/dev/api/acl/'.format(CONSTANTS[store_env]['sca_base_url']),
        method='POST', json=sca_data, headers=headers)
    root = response.json()['macaroon']

    caveat, = [
        c for c in Macaroon.deserialize(root).third_party_caveats()
        if c.location == CONSTANTS[store_env]['sso_location']
    ]

    sso_data = {
        'email': email,
        'password': getpass.getpass('Password for {}: '.format(email)),
        'otp': input('2FA (if enabled): '),
        'caveat_id': caveat.caveat_id,
    }
    response = requests.request(
        url='{}/api/v2/tokens/discharge'.format(CONSTANTS[store_env]['sso_base_url']),
        method='POST', json=sso_data, headers=headers)
    discharge = response.json()['discharge_macaroon']

    bound = Macaroon.deserialize(root).prepare_for_request(
        Macaroon.deserialize(discharge))

    return 'Macaroon root={}, discharge={}'.format(root, bound.serialize())


def main():
    parser = argparse.ArgumentParser(
        description='S(tore)URL ...'
    )
    parser.add_argument('-v', '--debug', action='store_true')
    parser.add_argument('-a', '--auth')

    parser.add_argument(
        '-e', '--email', default=os.environ.get('STORE_EMAIL'))
    parser.add_argument(
        '-s', '--store', default=os.environ.get('STORE_ENV', 'staging'))
    parser.add_argument(
        '-p', '--permission', nargs="*", dest='permissions',
        choices=['package_access', 'package_upload'])

    parser.add_argument('-I', dest='print_headers', action='store_true')
    parser.add_argument(
        '-X', '--method', default='GET', choices=['GET', 'POST'])
    parser.add_argument('-d', '--data')
    parser.add_argument('url', nargs='?')

    args = parser.parse_args()

    logger.setLevel(logging.INFO)
    if args.debug:
        logger.setLevel(logging.DEBUG)
        requests_log = logging.getLogger("requests.packages.urllib3")
        requests_log.setLevel(logging.DEBUG)
        requests_log.propagate = True


    if args.auth is not None and os.path.exists(args.auth):
        with open(os.path.expanduser(args.auth)) as fd:
            authorization = fd.read()
    else:
        if args.email is None:
            print('Needs "-e <email>" or $STORE_EMAIL.')
            return 1
        authorization = get_store_authorization(
            args.email, args.permissions, args.store)
        if args.auth is not None:
            with open(os.path.expanduser(args.auth), 'w') as fd:
                fd.write(authorization)

    headers = DEFAULT_HEADERS.copy()
    if args.url is None:
        url = '{}/dev/api/acl/verify/'.format(CONSTANTS[args.store]['sca_base_url'])
        data = {'auth_data': {'authorization': authorization}}
        method = 'POST'
    else:
        url = args.url
        if args.data is not None:
            if args.data.startswith('@'):
                with open(os.path.expanduser(args.data[1:])) as fd:
                    data = json.load(fd)
            else:
                data = json.loads(args.data)
            method = 'POST'
        else:
            data = None
            method = args.method
        headers.update({'Authorization': authorization})

    response = requests.request(
        url=url, method=method, json=data, headers=headers)

    if args.print_headers:
        print('HTTP/1.1 {} {}'.format(response.status_code, response.reason))
        for k, v in response.headers.items():
            print('{}: {}'.format(k, v))

    print(response.text)

    return 0


if __name__ == '__main__':
    sys.exit(main())