"""FoundationDB High Contention Counter.

NOTE: This is obsoleted for most practical purposes by the addition of atomic 
operations (transaction.add()) to FoundationDB 0.3.0, which do the same
thing more efficiently.

However, it serves as an example of a general technique, which might be useful
in optimizing some other associative, commutative operation which is not supported
directly by the database.

Provides the Counter class, which represents an integer value in the
database which can be incremented, added to, or subtracted from within
a transaction without conflict.

"""

import fdb
import fdb.tuple
import random
import os

fdb.api_version(200)

###########
# Counter #
###########

def _encode_int(i):
    return fdb.tuple.pack((i,)) # use the tuple layer to pack integers

def _decode_int(s):
    return fdb.tuple.unpack(s)[0]

def randID():
    return os.urandom(20) # this relies on good random data from the OS to avoid collisions

class Counter:
    """Represents an integer value which can be incremented without conflict.

    Uses a sharded representation (which scales with contention) along
    with background coalescing.

    """

    def __init__(self, db, subspace):
        self.subspace = subspace
        self.db = db

    def _coalesce(self, N):
        total = 0
        tr = self.db.create_transaction()
        try:

            # read N writes from a random place in ID space
            loc = self.subspace.pack((randID(),))
            if random.random() < 0.5:
                shards = tr.snapshot.get_range(loc, self.subspace.range().stop, limit=N);
            else:
                shards = tr.snapshot.get_range(self.subspace.range().start, loc, limit=N, reverse = True);

            # remove read shards transaction
            for k,v in shards:
                total += _decode_int(v)
                tr[k] # real read for isolation
                del tr[k]

            tr[self.subspace.pack((randID(),))] = _encode_int(total)

            ## note: no .wait() on the commit below--this just goes off
            ## into the ether and hopefully sometimes works :)
            ##
            ## the hold() function saves the tr variable so that the transaction
            ## doesn't get cancelled as tr goes out of scope!
            c = tr.commit()
            def hold(_,tr=tr): pass
            c.on_ready(hold)

        except fdb.FDBError as e:
            pass

    @fdb.transactional
    def get_transactional(self, tr):
        """Get the value of the counter.

        Not recommended for use with read/write transactions when the counter
        is being frequently updated (conflicts will be very likely).
        """
        total = 0
        for k,v in tr[self.subspace.range()]:
            total += _decode_int(v)
        return total

    @fdb.transactional
    def get_snapshot(self, tr):
        """
        Get the value of the counter with snapshot isolation (no
        transaction conflicts).
        """
        total = 0
        for k,v in tr.snapshot[self.subspace.range()]:
            total += _decode_int(v)
        return total

    @fdb.transactional
    def add(self, tr, x):
        """Add the value x to the counter."""

        tr[self.subspace.pack((randID(),))] = _encode_int(x)

        # Sometimes, coalesce the counter shards
        if random.random() < 0.1:
            self._coalesce(20)

    ## sets the counter to the value x
    @fdb.transactional
    def set_total(self, tr, x):
        """Set the counter to value x."""
        value = self.get_snapshot(tr)
        self.add(tr, x - value)

##################
# simple example #
##################

def counter_example_1(db, location):

    location = fdb.directory.create_or_open( db, ('tests','counter') )
    c = Counter(db, location)

    for i in range(500):
        c.add(db, 1)
    print c.get_snapshot(db) #500

#####################################
# high-contention, threaded example #
#####################################

def incrementer_thread(counter, db, n):
    for i in range(n):
        counter.add(db, 1)

def counter_example_2(db, location):
    import threading

    c = Counter(db, location)

    ## 50 incrementer_threads, each doing 10 increments
    threads = [
        threading.Thread(target=incrementer_thread, args=(c, db, 10))
        for i in range(50)]
    for thr in threads: thr.start()
    for thr in threads: thr.join()

    print c.get_snapshot(db) #500

if __name__ == "__main__":
    db = fdb.open()
    location = fdb.directory.create_or_open( db, ('tests','counter') )
    del db[location.range()]

    print "doing 500 inserts in 50 threads"
    counter_example_2(db, location)
    print len(db[:]), "counter shards remain in database"

    print "doing 500 inserts in one thread"
    counter_example_1(db, location)
    print len(db[:]), "counter shards remain in database"
