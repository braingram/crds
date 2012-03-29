"""This module is a command line script which handles comparing the best
reference recommendations for a particular context and dataset to prior bestrefs
recommendations for the dataset.

Dataset parameters/headers required to compute best refs can come in three
forms:

1. Dataset file headers
2. Recalibrate cache file
3. Database

Prior recommendations can really come in four forms:

1. Generated from a second context.
2. Dataset file headers
3. Recalibrate cache file
4. Database

To make new recommendations more quickly, recalibrate can store information
about prior recommendations in a cache file,  including both the recommendations
themselves and as well as critical parameters required to find them.

To support one possible use case of CRDS,  recalibrate can write new best
reference recommendations into the dataset file headers.
"""

import sys
import cPickle
import os.path
import pprint
import optparse

import pyfits

from crds import (log, rmap, data_file)

# ===================================================================

MISMATCHES = {}

# ===================================================================

class Cache(object):
    """A mapping which is kept in a file."""
    def __init__(self, filename, compute_value):
        """Load/save a mapping from `filename`,  calling `compute_value`
        whenever a key is sought which is not in the cache.
        """
        self.filename = filename
        self._compute_value = compute_value
        self._cache = {}
    
    def load(self):
        """Load the cache from it's file."""
        try:
            self._cache = cPickle.load(open(self.filename))
        except Exception, exc:
            log.verbose("Cache load failed:", str(exc), verbosity=25)
            self._cache = {}

    def save(self):
        """Save the cache to it's file."""
        cPickle.dump(self._cache, open(self.filename,"w+"))

    def get(self, key, args):
        """Get the cache value of `key`, calling the `compute_value`
        function with `args` if `key` is not in the cache.
        """
        if key in self._cache:
            log.verbose("Cache hit:", repr(key), verbosity=45)
        else:
            log.verbose("Cache miss:", repr(key), verbosity=45)
            self._cache[key] = self._compute_value(*args)
        return self._cache[key]
    
def get_recalibrate_info(context, dataset):
    """Fetch best reference parameters and results from `dataset`.
    
    `context` is only used as a helper to determine parkeys and
    filekinds,   not to determine bestref values.  All values
    are extracted from `dataset`s header.
    
    Return  ( {parkey: value, ...},  {filekind: bestref, ...} )
    """
    required_parkeys = context.get_minimum_header(dataset)
    filekinds = context.get_filekinds(dataset)
    parkey_values = data_file.get_header(dataset, required_parkeys)
    old_bestrefs = data_file.get_header(dataset, filekinds)
    old_bestrefs = { key.lower(): val.lower() \
                    for key, val in old_bestrefs.items()}
    return (parkey_values, old_bestrefs)
    
HEADER_CACHE = Cache("recalibrate.cache", get_recalibrate_info)

# ============================================================================

def recalibrate(new_context, datasets, old_context=None, update_datasets=False):
    """Compute best references for `dataset`s with respect to pipeline
    mapping `new_context`.  Either compare `new_context` results to 
    references from an `old_context` or compare to prior results recorded 
    in `dataset`s headers.   Optionally write new best reference
    recommendations to dataset headers.
    """
    for dataset in datasets:
        log.verbose("===> Processing", dataset, verbosity=25)

        basename = os.path.basename(dataset)
        
        try:
            header, old_bestrefs = HEADER_CACHE.get(
                basename, (new_context, dataset))
        except Exception:
            log.error("Can't get header info for " + repr(dataset))
            continue 

        bestrefs1 = trapped_bestrefs(new_context, header)

        if old_context:
            bestrefs2 = trapped_bestrefs(old_context, header)
            old_fname = old_context.filename
        else:
            bestrefs2 = old_bestrefs
            old_fname = "<dataset prior results>"
            
        if not bestrefs1 or not bestrefs2:
            log.error("Skipping comparison for", repr(dataset))
            continue
        
        new_fname = os.path.basename(new_context.filename)
        
        compare_bestrefs(new_fname, old_fname, dataset, bestrefs1, bestrefs2)
        
        if update_datasets:
            write_bestrefs(new_fname, dataset, bestrefs1)
            
    log.write("Reference Changes:")
    log.write(pprint.pformat(MISMATCHES))

def trapped_bestrefs(ctx, header):
    """Compute and return bestrefs or convert exceptions to ERROR messages
    and return None.
    """
    try:
        return ctx.get_best_references(header)
    except Exception:
        log.error("Best references FAILED for ", repr(ctx))

def compare_bestrefs(ctx1, ctx2, dataset, bestrefs1, bestrefs2):
    """Compare two sets of best references for `dataset` taken from
    contexts named `ctx1` and `ctx2`.
    """
    mismatches = 0
    
    # Warn about mismatched filekinds
    check_same_filekinds(ctx1, ctx2, bestrefs1, bestrefs2)
    check_same_filekinds(ctx2, ctx1, bestrefs2, bestrefs1)

    for filekind in bestrefs1:
        if filekind not in bestrefs2:
            continue
        new = remove_irafpath(bestrefs1[filekind])
        old = remove_irafpath(bestrefs2[filekind])
        if isinstance(old, (str, unicode)):
            old = str(old).strip().lower()
        if old not in [None, "", "n/a","*"]:
            if new != old:
                log.info("New Reference for",  repr(dataset), repr(filekind), 
                            "is", repr(new), "was", repr(old))
                if filekind != "mdriztab":  
                    # these are guaranteed to fail for archive files
                    mismatches += 1
                    if filekind not in MISMATCHES:
                        MISMATCHES[filekind] = []
                    MISMATCHES[filekind].append(dataset)
            else:
                log.verbose("Lookup MATCHES for", repr(filekind), repr(new), 
                            verbosity=30)
        else:
            log.verbose("Lookup N/A for", repr(filekind), repr(new),
                        verbosity=30)
    if mismatches > 0:
        sys.exc_clear()
        log.verbose("Total New References for", repr(dataset), "=", mismatches,
                 verbosity=25)
    else:
        log.verbose("All lookups for", repr(dataset), "MATCH.", verbosity=25)

def check_same_filekinds(ctx1, ctx2, bestrefs1, bestrefs2):
    """Verify all the filekinds in `bestrefs1` also exist in `bestrefs2`."""
    for filekind in bestrefs1:
        if filekind not in bestrefs2:
            log.warning("Filekind", repr(filekind), "recommended by", 
                        repr(ctx1), "but not", repr(ctx2))
    
def remove_irafpath(name):
    """jref$n4e12510j_crr.fits  --> n4e12510j_crr.fits"""
    return name.split("$")[-1]

def write_bestrefs(new_fname, dataset, bestrefs):
    """Update the header of `dataset` with best reference recommendations
    `bestrefs` determined by context named `new_fname`.
    """
    pyfits.setval(dataset, "CRDS_CTX", new_fname)
    for key, value in bestrefs.items():
#        XXX what to do here for failed lookups?
#        if value.startswith("NOT FOUND"):
#            value = value + ", prior " + pyfits.getval(dataset, key)
        pyfits.setval(dataset, key, value)

# =============================================================================

def main():
    """Process command line parameters and run recalibrate."""
    parser = optparse.OptionParser(
        "usage: %prog [options] <new_context> <datasets...>")
    parser.add_option("-c", "--cache-headers", dest="use_cache",
        help="Use and/or remember critical header parameters in a cache file.", 
        action="store_true")
    parser.add_option("-f", "--files", dest="files",
        help="Read datasets from FILELIST, one dataset per line.", 
        metavar="FILELIST", default=None)
    parser.add_option("-o", "--old-context", dest="old_context",
        help="Compare best refs recommendations from two contexts.", 
        metavar="OLD_CONTEXT", default=None)
    parser.add_option("-u", "--update-datasets", dest="update_datasets",
        help="Update dataset headers with new best reference recommendations.", 
        action="store_true")
    options, args = log.handle_standard_options(sys.argv, parser=parser)

    if len(args) < 2:
        log.write("usage: recalibrate.py <pmap>  <dataset>... [options]")
        sys.exit(-1)

    newctx_fname, datasets = args[1], args[2:]
    
    if options.files:
        datasets += [file_.strip() for file_ in open(options.files).readlines()]
    
    # do one time startup outside profiler.
    newctx = rmap.get_cached_mapping(newctx_fname)
    if options.old_context:
        oldctx = rmap.get_cached_mapping(options.old_context)
    else:
        oldctx = None
        
    if options.use_cache:
        HEADER_CACHE.load()
    
    log.standard_run(
        "recalibrate(newctx, datasets, oldctx, options.update_datasets)", 
        options, globals(), locals())

    if options.use_cache:
        HEADER_CACHE.save()

    log.standard_status()

if __name__ == "__main__":
    main()
