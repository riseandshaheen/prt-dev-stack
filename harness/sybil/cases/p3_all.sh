#!/bin/bash
S=${CASES_RUN:-/tmp/prt-cases}
for c in m5 p1 l3 k4 k7 k3a k3b; do bash $S/run/p3.sh $c > $S/run/out/p3-$c.log 2>&1; done
echo ALL_DONE > $S/run/out/p3-all.done
