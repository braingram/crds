"""This module defines a base class for CRDS command line scripts.

MAYBE integrate rc, environment, and command line parameters.
"""
import sys
import argparse
import pdb
import cProfile as profile
import re

from argparse import RawTextHelpFormatter

from crds import rmap, log, data_file, heavy_client, config
from crds.client import api

# =============================================================================

def _show_version():
    """Dump CRDS version information and exit python."""
    import crds, crds.svn_version
    log.info("crds version", crds.__version__, "revision", crds.svn_version.__svn_version__)
    sys.exit(-1)

# =============================================================================

# command line parameter type coercion / verification functions

def dataset(filename):
    """Ensure `filename` names a dataset."""
    if data_file.is_dataset(filename):
        return filename
    else:
        raise ValueError("Parameter " + repr(filename) + " does not appear to be a dataset filename.")

def reference_file(filename):
    """Ensure `filename` is a reference file."""
    assert filename.endswith((".fits", ".finf")), "A .fits or .finf file is required but got: '%s'" % filename
    return filename

def mapping(filename):
    """Ensure `filename` is a CRDS mapping file."""
    assert config.is_mapping(filename), "A .rmap, .imap, or .pmap file is required but got: '%s'" % filename
    return filename

def mapping_spec(spec):
    """Ensure `spec` is a CRDS mapping specification, a filename or a date based spec."""
    assert config.is_mapping_spec(spec), "A .rmap, .imap, or .pmap file or date base specification is required but got: '%s'" % spec
    return spec

def pipeline_mapping(filename):
    """Ensure `filename` is a .pmap file."""
    assert filename.endswith(".pmap"), "A .pmap file is required but got: '%s'" % filename
    return filename

def instrument_mapping(filename):
    """Ensure `filename` is a .imap file."""
    assert filename.endswith(".imap"), "A .imap file is required but got: '%s'" % filename
    return filename

def reference_mapping(filename):
    """Ensure `filename` is a .rmap file."""
    assert filename.endswith(".rmap"), "A .rmap file is required but got: '%s'" % filename
    return filename

#def mapping(filename):
#    """Ensure that `filename` is any known CRDS mapping."""
#    if api.is_known_mapping(filename):
#        return filename
#    else:
#        raise ValueError("Parameter " + repr(filename) + " is not a known CRDS mapping.")

def observatory(obs):
    """Verify that `obs` is the name of an observatory and return it."""
    obs = obs.lower()
    assert obs in ["hst", "jwst", "tobs"], "Unknown observatory " + repr(obs)
    return obs

def nrange(string):
    """Verify a context range expression MIN:MAX and return (MIN, MAX)."""
    assert re.match(r"\d+:\d+", string), \
        "Invalid context range specification " + repr(string)
    rmin, rmax = [int(x) for x in string.split(":")]
    assert 0 <= rmin <= rmax, "Invalid range values"
    return rmin, rmax
    

# =============================================================================

class Script(object):
    """Base class for CRDS command line scripts with standard properties.
    
    `args` is either a string of command line parameters or a parameter list of command line words.  If
           defaulted to None then `args` is treated as sys.argv (default argparse handling). Note that `args`
           shoulld include the program name as args[0].  Explicitly specifying `args` is used to
           instantiate a script in code for testing, etc.
    """
    
    decription = epilog = usage = None
    formatter_class = RawTextHelpFormatter
    
    def __init__(self, argv=None, parser_pars=None):
        if isinstance(argv, basestring):
            argv = argv.split()
        elif argv is None:
            argv = sys.argv
        self._argv = argv
        if parser_pars is None:
            parser_pars = {}
        self._server_info = None
        for key in ["description", "epilog", "usage", "formatter_class"]: 
            self._add_key(key, parser_pars)
        self.parser = argparse.ArgumentParser(prog=argv[0], **parser_pars)
        self.add_args()
        self.add_standard_args()
        self.args = self.parser.parse_args(argv[1:])
        log.set_verbose(self.args.verbosity or self.args.verbose)
        self.contexts = self.determine_contexts()
        
    def main(self):
        """Write a main method to perform the actions of the script using self.args."""
        raise NotImplementedError("Script subclasses have to define main().")
    
    def determine_contexts(self):
        return []    

    def add_args(self):
        """Add script-specific argparse add_argument calls here on self.parser"""
        raise NotImplementedError("Script subclasses have to define add_args().")
    
    @property
    def observatory(self):
        """Return either the command-line override observatory,  or the one determined
        by the client/server exchange.
        """
        obs = None
        if self.args.jwst:
            obs = "jwst"
        if self.args.hst:
            assert obs in [None, "hst"], "Ambiguous observatory. Only work on HST or JWST files at one time."
            obs = "hst"
        if hasattr(self.args, "files"):
            files = self.args.files if self.args.files else []
            for file in files:
                if file.startswith("hst"):
                    assert obs in [None, "hst"], "Ambiguous observatory. Only work on HST or JWST files at one time."
                    obs = "hst"
                if file.startswith("jwst"):
                    assert obs in [None, "jwst"], "Ambiguous observatory. Only work on HST or JWST files at one time."
                    obs = "jwst"
        for file in self.contexts:
            if file.startswith("hst"):
                assert obs in [None, "hst"], "Ambiguous observatory. Only work on HST or JWST files at one time."
                obs = "hst"
            if file.startswith("jwst"):
                assert obs in [None, "jwst"], "Ambiguous observatory. Only work on HST or JWST files at one time."
                obs = "jwst"
        if obs is None:
            obs = api.get_default_observatory()
        return obs
        
    def _add_key(self, key, parser_pars):
        """Add any defined class attribute for `key` to dict `parser_pars`."""
        inlined = getattr(self, key, parser_pars)
        if inlined is not None:  
            parser_pars[key] = inlined
        return parser_pars
    
    def add_argument(self, *args, **keys):
        """Add a parser argument."""
        self.parser.add_argument(*args, **keys)

    def add_standard_args(self):
        """Add standard CRDS command line parameters."""
        self.add_argument("-v", "--verbose", 
            help="Set log verbosity to True,  nominal debug level.", action="store_true")
        self.add_argument("--verbosity", 
            help="Set log verbosity to a specific level: 0..100.", type=int, default=0)
        self.add_argument("-V", "--version", 
            help="Print the software version and exit.", action="store_true")
        self.add_argument("-J", "--jwst", dest="jwst", action="store_true",
            help="Force observatory to JWST for determining header conventions.""")
        self.add_argument("-H", "--hst",  dest="hst", action="store_true",
            help="Force observatory to HST for determining header conventions.""")
        self.add_argument("--profile", 
            help="Output profile stats to the specified file.", type=str, default="")
        self.add_argument("--pdb", 
            help="Run under pdb.", action="store_true")
    
    def test_server_connection(self):
        """Check the server connection and remember the server_info."""
        connected, server_info = heavy_client.get_config_info(self.observatory)
        log.verbose("CRDS server info", server_info)
        if not connected:
            log.error("Failed connecting to CRDS server at", repr(api.get_crds_server()))
            sys.exit(-1)
        return server_info
            
    @property
    def server_info(self):
        """Return the server_info dict from the CRDS server."""
        if self._server_info is None:
            self._server_info = self.test_server_connection()
        return self._server_info

    @property
    def default_context(self):
        """Return the default operational .pmap defined by the CRDS server or cache."""
        return self.server_info["operational_context"]

    def get_files(self, file_list):
        """Process a file list,  expanding @-files into corresponding lists of
        files.   Return a flat, depth-first,  file list.
        """
        files = []
        for fname in file_list:
            if fname.startswith("@"):
                files.extend(self.load_file_list(fname[1:]))
            else:
                files.append(fname)
        return files
    
    def load_file_list(self, at_file):
        """Recursively load an @-file, returning a list of words/files.
        Any stripped line beginning with # is a comment line to be ignored.
        Any word beginning with @ is a file to load recursively.
        Each line is split into words/files using whitespace.
        """
        files = []
        with open(at_file) as atf:
            for line in atf.readlines():
                fname = line.strip()
                if fname.startswith("#"):
                    continue
                if fname.startswith("@"):
                    more = self.load_file_list(fname[1:])
                else:
                    more = fname.split()
                files.extend(more)
        return self.get_files(files)   # another pass to fix paths

    @property
    def files(self):
        """Handle @-files and add cache_paths to command line file parameters.
        Nominally self.files are assumed to be references or mappings.  Override locate_file()
        to handle other files.
        """ 
        if not hasattr(self.args, "files"):
            raise NotImplementedError("Class must implement list of `self.args.files` raw file paths.")
        return [self.locate_file(fname) for fname in self.get_files(self.args.files)]
        
    def locate_file(self, filename):
        """Locate file defines how members of the self.args.files list are located when they have
        no absolute or relative path.   The default behavior is to locate CRDS cached files,  either
        references or mappings.   This is inappropriate for datasets so in some cases locate_file
        needs to be overridden.
        """
        return rmap.locate_file(filename, observatory=self.observatory)

    def __call__(self):
        """Run the script's main() according to command line parameters."""
        if self.args.version:
            _show_version()
        elif self.args.profile:
            profile.runctx("self.main()", locals(), locals(), self.args.profile)
        elif self.args.pdb:
            pdb.runctx("self.main()", locals(), locals())
        else:
            self.main()

    def run(self, *args, **keys):
        """script.run() is the same thing as script() but more explicit."""
        self.__call__(*args, **keys)
        
    def resolve_context(self, context):
        """Resolve context spec `context` into a .pmap, .imap, or .rmap filename,  interpreting
        date based specifications against the CRDS server operational context history.
        """
        assert config.is_mapping_spec(context), \
            "Invalid .pmap, .imap, or .imap filename or date based context specification."
        if config.is_date_based_mapping_spec(context):
            resolved_context = api.get_context_by_date(context)
            log.verbose("Date based context", repr(context), "resolves to", repr(resolved_context))
            context = resolved_context
        return context

# =============================================================================

class ContextsScript(Script):
    """Baseclass for a script proving support for command line specified contexts."""
    
    def __init__(self, *args, **keys):
        super(ContextsScript, self).__init__(*args, **keys)

    def add_args(self):
        self.add_argument('--contexts', metavar='CONTEXT', type=mapping_spec, nargs='*',
            help="Specify a list of CRDS mappings to operate on: .pmap, .imap, or .rmap or date-based specification")        
        self.add_argument("--range", metavar="MIN:MAX",  type=nrange, dest="range", default=None,
            help='Operate for pipeline context ids (.pmaps) between <MIN> and <MAX>.')
        self.add_argument('--all', action='store_true',
            help='Operate with respect to all known CRDS contexts.')

    def determine_contexts(self):
        """Support explicit specification of contexts, context id range, or all."""
        if self.args.contexts:
            assert not self.args.range, 'Cannot specify explicit contexts and --range'
            assert not self.args.all, 'Cannot specify explicit contexts and --all'
            # permit instrument and reference mappings,  not just pipelines:
            contexts = [self.resolve_context(ctx) for ctx in self.args.contexts]
        elif self.args.all:
            assert not self.args.range, "Cannot specify --all and --range"
            contexts = api.list_mappings(glob_pattern="*.pmap")
        elif self.args.range:
            rmin, rmax = self.args.range
            contexts = []
            all_contexts = api.list_mappings(glob_pattern="*.pmap")
            for context in all_contexts:
                match = re.match(r"\w+_(\d+).pmap", context)
                if match:
                    serial = int(match.group(1))
                    if rmin <= serial <= rmax:
                        contexts.append(context)
        else:
            contexts = []
        return sorted(contexts)
    
    def get_context_mappings(self):
        """Return the set of mappings which are pointed to by the mappings
        in `contexts`.
        """
        files = set()
        for context in self.contexts:
            pmap = rmap.get_cached_mapping(context)
            files = files.union(pmap.mapping_names())
        return sorted(files)
    
    def get_context_references(self):
        """Return the set of mappings which are pointed to by the mappings
        in `contexts`.
        """
        files = set()
        for context in self.contexts:
            files = files.union(api.get_reference_names(context))
        return sorted(files)

    def main(self):
        """Write a main method to perform the actions of the script using self.args."""
        raise NotImplementedError("ScriptWithContexts subclasses have to define main().")
    
