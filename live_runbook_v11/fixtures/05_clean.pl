#!/usr/bin/perl
use strict;
use warnings;

sub format_amount {
    my ($value) = @_;
    return sprintf("%.2f", $value);
}

sub greet_user {
    my ($name) = @_;
    return "Hello, " . $name;
}

1;
