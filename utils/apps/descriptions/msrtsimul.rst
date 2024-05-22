msrtsimul simulates a real-time data acquisition by injecting miniSEED data from a
file into the seedlink buffer via the mseedfifo plugin for seedlink. It can be
used for simulating real-time conditions in playbacks for whole-system
demonstrations, user training, etc.

The data is played back as if they were recorded at current time. Therefore,
creation times and the actual data times including pick times, event times etc.
will be **obscured**. :ref:`Historic playbacks <sec-msrtsimul-historic>` allow
keeping the actual data times.

.. hint::

   * Playbacks on production systems are normally not recommended.
   * For real-time playbacks, the data must be sorted by end time. This
     requirement may be violated. Use :ref:`scmssort` for sorting the data by
     (end) time.
   * Stop :ref:`slarchive` before running msrtsimul for avoiding that data with
     wrong times are archived.
   * Normally, :ref:`seedlink` assumes that the data is provided in records of
     512 bytes. msrtsimul issues a warning when detecting a record of other size.
   * Data available in other record sizes can be repacked to 512 bytes by
     external software such as :program:`msrepack` available with
     :cite:t:`libmseed-github`.
   * Applications other than standard :ref:`seedlink` in |scname| or
     :ref:`seedlink` compiled specifically may accept other record sizes. For
     accepting these records use msrtsimul with :option:`--unlimited`.


Non-default seedlink pipes
--------------------------

By default, msrtsimul writes the data into the mseedfifo pipe
*$SEISCOMP_ROOT/var/run/seedlink/mseedfifo*.
If the data is to be written into the pipe of a :program:`seedlink` alias or
into any other pipe, the pipe name must be adjusted. Use the option

* :option:`--seedlink` to replace *seedlink* by another name, e.g. a seedlink instance
  created as an alias, **seedlink-test**. This would write into
  *$SEISCOMP_ROOT/var/run/seedlink-test/mseedfifo*.
* :option:`--stdout` to write to standard output and then redirect to any other location.


.. _sec-msrtsimul-historic:

Historic playbacks
------------------

You may use msrtsimul with the :option:`-m` *historic* option to maintain the
time of the records,
thus the times of picks, amplitudes, origins, etc. but not the creation times.
Applying :option:`-m` *historic* will feed the data into the seedlink buffer at the time
of the records. The time of the system is untouched. GUI, processing modules, logging,
etc. will run with current system time. The historic mode allows to process waveforms
with the stream inventory valid at the time when the data were recorded including
streams closed at current time.

.. warning ::

   When repeating historic playbacks, the waveforms are fed multiple times to the
   seedlink buffer and the resulting picks are also repeated with the same pick
   times. This may confuse the real-time system. Therefore, seedlink and other modules
   creating or processing picks should be
   stopped, the seedlink buffer should be cleared and the processing
   modules should be restarted to clear the buffers before starting the
   historic playbacks. Make sure :ref:`scautopick` is configured or started with
   the :option:`--playback` option. Example:

   .. code-block:: sh

      seiscomp stop
      rm -rf $SEISCOMP_ROOT/var/lib/seedlink/buffer
      seiscomp start
      msrtsimul ...


seedlink setup
--------------

For supporting msrtsimul activate the :confval:`msrtsimul` parameter in the
seedlink module configuration (:file:`seedlink.cfg`), update the configuration
and restart seedlink before running msrtsimul:

.. code-block:: sh

   seiscomp update-config seedlink
   seiscomp restart seedlink
   msrtsimul ...


Examples
--------

1. Playback miniSEED waveforms in real time with verbose output:

   .. code-block:: sh

      $ msrtsimul -v miniSEED-file

#. Playback miniSEED waveforms in historic mode. This may require :ref:`scautopick`
   to be started with the option *playback*:

   .. code-block:: sh

      msrtsimul -v -m historic miniSEED-file

#. Feed the data into the buffer of a specific seedlink instance, e.g. *seedlink-test*:

   .. code-block:: sh

      msrtsimul -v --seedlink seedlink-test miniSEED-file
