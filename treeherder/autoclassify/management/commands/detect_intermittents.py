import logging

from django.core.management.base import (BaseCommand,
                                         CommandError)

from treeherder.autoclassify import detectors
from treeherder.model.derived import JobsModel
from treeherder.model.models import (FailureLine,
                                     Matcher)

from .autoclassify import match_errors

logger = logging.getLogger(__name__)

detectors.register()


class Command(BaseCommand):
    args = '<job_guid>, <repository>'
    help = 'Look for new intermittents in a job'

    def handle(self, *args, **options):
        if not len(args) == 2:
            raise CommandError('2 arguments required, %s given' % len(args))
        job_guid, repository = args

        with JobsModel(repository) as jobs_model:
            jobs = jobs_model.get_job_repeats(job_guid)

        add_new_intermittents(repository, jobs)


def add_new_intermittents(repository, jobs):
    # The approach here is currently to look for new intermittents to add, one at a time
    # and then rerun the matching on other jobs
    # TODO: limit the possible matches to those that have just been added
    if len(jobs) <= 1:
        logger.info("Too few jobs in the current set")
        return

    # For now conservatively assume that we can only mark new intermittents if
    # one run in the current set fully passes
    if not any(job["result"] == "success" for job in jobs):
        logger.info("No successful jobs to compare against")
        return

    failures_by_job = FailureLine.objects.for_jobs(*jobs)

    for job in jobs:
        logger.debug("Looking for new intermittents from job %s" % (job["job_guid"]))
        if not job["job_guid"] in failures_by_job:
            logger.debug("Job has no failures")
            continue

        new_matches = set()

        for detector in Matcher.objects.registered_detectors():
            job_failures = failures_by_job[job["job_guid"]]

            unmatched_lines = [item for item in job_failures if
                               not item.classified_failures.count() and
                               item.id not in new_matches]

            logger.debug("Unmatched lines %r" % unmatched_lines)
            if unmatched_lines:
                logger.debug("Found %i unmatched lines" % len(unmatched_lines))
            line_indicies = detector(unmatched_lines)

            for index in line_indicies:
                failure = unmatched_lines[index]
                failure.create_new_classification(detector.db_object)
                new_matches.add(failure.id)

        if new_matches:
            for rematch_job in jobs:
                if rematch_job == job:
                    continue
                logger.debug("Trying rematch on job %s" % (rematch_job["job_guid"]))
                match_errors(repository, rematch_job["job_guid"])
