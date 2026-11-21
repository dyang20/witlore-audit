# Derived site inputs

The tranche release sheet has no row for TI-OPP-43986, though a completed human
audit for it exists. These values are read off that audit's header:

    FA Number: 10589537   Coordinates: 41.303639, -95.385222
    Search Radius: 3,281'  Requested Rad: 215'

They are therefore NOT independent of the report being compared against. Runs
using this file pass `--derived`, which tags the site `derived_from_report` in
the evidence ledger and prints a note in the generated audit.

The site is in Oakland, IA, split between the City of Oakland and
unincorporated Pottawattamie County. Note the audit header says Customer Market
MICHIGAN/INDIANA and Customer Region East, which looks like a copy-paste error
in their document: the coordinates are in Iowa. Recorded as written.
