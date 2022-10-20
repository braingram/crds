"""This module codifies standard practices for scripted interactions with the
web server file submission system.
"""
import os
import io

from crds.core import log, utils
from crds.core.exceptions import CrdsError, CrdsWebError
from . import background

# from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor
DISABLED = []
try:
    import requests
except (ImportError, RuntimeError):
    log.verbose_warning("Import of 'requests' failed.  submit disabled.")
    DISABLED.append("requests")
try:
    from lxml import html
except (ImportError, RuntimeError):
    log.verbose_warning("Import of 'lxml' failed.  submit disabled.")
    DISABLED.append("lxml")

# ==================================================================================================

def log_section(section_name, section_value, verbosity=50, log_function=log.verbose,
                divider_name=None):
    """Issue log divider bar followed by a corresponding log message."""
    log.divider(name=divider_name, verbosity=verbosity, func=log.verbose)
    log_function(section_name, section_value, verbosity=verbosity+5)

# ==================================================================================================

_UPLOAD_CHUNK_SIZE = 2 * 1000 * 1000

class CrdsDjangoConnection:

    """This class handles CRDS authentication, basic GET, basic POST, and CRDS-style get/post.
    It also manages the CSRF token generated by Django to block form forgeries and CRDS instrument
    management/locking.
    """

    def __init__(self, locked_instrument="none", username=None, password=None, base_url=None):
        if DISABLED:
            raise CrdsError("Missing or broken depenencies:", DISABLED)
        self.locked_instrument = locked_instrument
        self.username = username
        self.password = password
        self.base_url = base_url
        self.session = requests.session()
        self.session.headers.update({'referer': self.base_url})

    def abs_url(self, relative_url):
        """Return the absolute server URL constructed from the given `relative_url`."""
        return self.base_url + relative_url

    def dump_response(self, name, response):
        """Print out verbose output related to web `response` from activity `name`."""
        log_section("headers:\n", response.headers, divider_name=name, verbosity=70)
        log_section("status_code:", response.status_code, verbosity=50)
        log_section("text:\n", response.text, verbosity=75)
        try:
            json_text = response.json()
            log_section("json:\n", json_text)
        except Exception:
            pass
        log.divider(func=log.verbose)

    def response_complete(self, args):
        """Wait for an aysnchronous web response, do debug logging,  check for errors."""
        response = background.background_complete(args)
        self.dump_response("Response: ", response)
        self.check_error(response)
        return response

    post_complete = get_complete = repost_complete = response_complete

    def get(self, relative_url):
        """HTTP(S) GET `relative_url` and return the requests response object."""
        args = self.get_start(relative_url)
        return self.get_complete(args)

    @background.background
    def get_start(self, relative_url):
        """Initiate a GET running in the background, do debug logging."""
        url = self.abs_url(relative_url)
        log_section("GET:", url, divider_name="GET: " + url.split("&")[0])
        return self.session.get(url)

    def post(self, relative_url, *post_dicts, **post_vars):
        """HTTP(S) POST `relative_url` and return the requests response object."""
        args = self.post_start(relative_url, *post_dicts, **post_vars)
        return self.post_complete(args)

    @background.background
    def post_start(self, relative_url, *post_dicts, **post_vars):
        """Initiate a POST running in the background, do debug logging."""
        url = self.abs_url(relative_url)
        vars = utils.combine_dicts(*post_dicts, **post_vars)
        log_section("POST:", vars, divider_name="POST: " + url)
        return self.session.post(url, data=vars)

    def repost(self, relative_url, *post_dicts, **post_vars):
        """First GET form from ``relative_url`,  next POST form to same
        url using composition of variables from *post_dicts and **post_vars.

        Maintain Django CSRF session token.
        """
        args = self.repost_start(relative_url, *post_dicts, **post_vars)
        return self.repost_complete(args)

    def repost_start(self, relative_url, *post_dicts, **post_vars):
        """Initiate a repost,  first getting the form synchronously and extracting
        the csrf token,  then doing a post_start() of the form and returning
        the resulting thread and queue.
        """
        response = self.get(relative_url)
        csrf_values= html.fromstring(response.text).xpath(
            '//input[@name="csrfmiddlewaretoken"]/@value'
            )
        if csrf_values:
            post_vars['csrfmiddlewaretoken'] = csrf_values[0]
        return self.post_start(relative_url, *post_dicts, **post_vars)
    
    def repost_confirm_or_cancel(self, ready_url, action="confirm"):
        relative_url = '/' + '/'.join(ready_url.split('/')[-2:])
        results_id = relative_url.split('/')[-1]
        resp = self.get(relative_url)
        csrf = resp.cookies['csrftoken']
        url = self.abs_url('/submit_confirm_pipeline/')
        response = self.session.post(url, {
            'results_id': results_id,
            'csrfmiddlewaretoken': csrf,
            'button': action,
            })
        self.dump_response("Response: ", response)
        self.check_error(response)
        return response


    """
    {'time_remaining': '3:57:58', 'user': 'jmiller_unpriv', 'created_on': '2017-02-23 16:12:55', 'type': 'instrument', 'is_expired': False, 'status': 'ok', 'name': 'miri'}
    """
    def fail_if_existing_lock(self):
        """Issue a warning if self.locked_instrument is already locked."""
        response = self.get("/lock_status/"+self.username+"/")
        log.verbose("lock_status:", response)
        json_dict = utils.Struct(response.json())
        if (json_dict.name and (not json_dict.is_expired) and (json_dict.type == "instrument") and (json_dict.user == self.username)):
            CrdsWebError("User", repr(self.username), "has already locked", repr(json_dict.name),
                         ".  Failing to avert collisions.  User --logout or logout on the website to bypass.")

    def login(self, next="/"):
        """Login to the CRDS website and proceed to relative url `next`."""
        self.session.cookies["ASB-AUTH"] = self.password
        response = self.repost(
            "/login/",
            username = self.username,
            password = self.password,
            instrument = self.locked_instrument,
            next = next,
            )
        self.check_login(response)

    def check_error(self, response):
        """Note an error + exception if response contains an error_message <div>."""
        self._check_error(response, '//div[@id="error_message"]', "CRDS server error:")
        self._check_error(response, '//div[@class="error_message"]', "CRDS server new form error:")

    def check_login(self, response):
        """Note an error + exception if response contains content indicating login error."""
        self._check_error(
            response, '//div[@id="error_login"]',
            "Error logging into CRDS server:")
        self._check_error(
            response, '//div[@id="error_message"]',
            "Error logging into CRDS server:")
        self._check_error(
            response, '//title[contains(text(), "MyST SSO Portal")]',
            "Error logging into CRDS server:")

    def _check_error(self, response, xpath_spec, error_prefix):
        """Extract the `xpath_spec` text from `response`,  if present issue a
        log ERROR with  `error_prefix` and the response `xpath_spec` text
        then raise an exception.  This may result in multiple ERROR messages.

        Issue a log ERROR for each form error,  then raise an exception
        if any errors found.

        returns None
        """
        errors = 0
        if response.ok:
            error_msg_parse = html.fromstring(response.text).xpath(xpath_spec)
            for parse in error_msg_parse:
                error_message = parse.text.strip().replace("\n","")
                if error_message:
                    if error_message.startswith("ERROR: "):
                        error_message = error_message[len("ERROR: ")]
                    errors += 1
                    log.error(error_prefix, error_message)
        else:
            log.error("CRDS server responded with HTTP error status", response.status_code)
            errors += 1

        if errors:
            raise CrdsWebError("A web transaction with the CRDS server had errors.")

    def logout(self):
        """Login to the CRDS website and proceed to relative url `next`."""
        self.get("/logout/")

    def upload_file(self, filepath):
        abs_url = self.abs_url("/upload/chunked/")
        response = self.session.get(abs_url)
        log.verbose("COOKIES:", log.PP(response.cookies))
        csrf_token = response.cookies["csrftoken"]
        file_size = os.stat(filepath).st_size
        filename = os.path.basename(filepath)

        if file_size < _UPLOAD_CHUNK_SIZE:
            files = {"files": (filename, open(filepath, "rb"))}
            data = {"csrfmiddlewaretoken": csrf_token}
            self.session.post(abs_url, files=files, data=data)
        else:
            with open(filepath, "rb") as f:
                start_byte = 0
                while True:
                    chunk = f.read(_UPLOAD_CHUNK_SIZE)
                    if len(chunk) == 0:
                        break

                    files = {"files": (filename, io.BytesIO(chunk))}
                    data = {"csrfmiddlewaretoken": csrf_token}
                    end_byte = start_byte + len(chunk) - 1
                    content_range = f"bytes {start_byte}-{end_byte}/{file_size}"
                    headers = {"Content-Range": content_range}
                    response = self.session.post(abs_url, files=files, data=data, headers=headers)
                    csrf_token = response.cookies["csrftoken"]
                    start_byte = end_byte + 1
