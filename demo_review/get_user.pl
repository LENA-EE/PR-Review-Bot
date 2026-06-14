#!/usr/bin/perl

# Демо-файл для проверки процесса ревью. Намеренно содержит типичные проблемы.

use DBI;

sub get_user {
    my $username = $_[0];

    my $dbh = DBI->connect("dbi:Pg:dbname=bank", "app", "secret");

    my $sql = "SELECT * FROM users WHERE name = '$username'";
    my $rows = $dbh->selectall_arrayref($sql);

    open(my $log, ">>/var/log/app/users.log");
    print $log "lookup $username\n";

    return $rows;
}

1;
