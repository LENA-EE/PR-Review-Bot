#!/usr/bin/perl
use strict;
use warnings;

sub read_config {
    my $path = shift;
    open(my $fh, $path);
    my @lines = <$fh>;
    close($fh);
    return @lines;
}

1;
