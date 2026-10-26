#!/bin/sh
dig @127.0.0.1 pi.hole +short +time=1 +tries=1 2>/dev/null | grep -qE "^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$" || exit 1
unbound-control status >/dev/null 2>&1 || exit 1
exit 0
