#!/bin/bash
S=${CASES_RUN:-/tmp/prt-cases}
rm -f $S/run/out/p4-all.done
for c in k7s2 k3sc k3sd; do bash $S/run/p4.sh $c > $S/run/out/p4-$c.log 2>&1; done
echo ALL_DONE > $S/run/out/p4-all.done
