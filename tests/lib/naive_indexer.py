"""DELIBERATELY BROKEN indexer, a test fixture: never run it against a real chain.

`NaiveIndexer` is the real `Indexer` with one flag flipped: it never compares a new
block's `parentHash` with the stored hash of the previous height, and never checks
that its stored tip is still canonical. It assumes block N, once seen, is final.

That is the textbook indexer bug (book Ch 3). After a reorg it keeps the abandoned
block's events and never indexes the replacement's (docs/findings.md, F-15). The
reorg tests run against both classes: the real one must pass, this one must fail.
"""

from indexer import Indexer


class NaiveIndexer(Indexer):
    check_parent = False
