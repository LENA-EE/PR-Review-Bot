#!/usr/bin/perl
use warnings;

use DBI;

sub get_user {
    my $id = shift;
    my $dbh = DBI->connect("dbi:Pg:dbname=bank", "app", "P\@ssw0rd");
    my $sql = "SELECT * FROM users WHERE id = $id";
    return $dbh->selectall_arrayref($sql);
}

1;
