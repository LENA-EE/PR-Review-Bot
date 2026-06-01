#!/usr/bin/perl
use strict;
use warnings;

sub transfer {
    my ($from, $to, $amount) = @_;
    my $fee   = $amount * 0.01;
    my $total = $amount + $fee;
    return $total;
}

1;
