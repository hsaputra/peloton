import pytest

from tests.integration.aurorabridge_test.client import api
from tests.integration.aurorabridge_test.util import (
    get_job_update_request,
    get_update_status,
    start_job_update,
    wait_for_update_status,
)

pytestmark = [pytest.mark.default,
              pytest.mark.aurorabridge,
              pytest.mark.random_order(disabled=True)]


def test__start_job_update_rolled_forward(client):
    start_job_update(
        client,
        'test_dc_labrat.yaml',
        'start job update test/dc/labrat')


def test__start_job_update_with_pulse(client):
    req = get_job_update_request('test_dc_labrat_pulsed.yaml')
    res = client.start_job_update(req, 'start pulsed job update test/dc/labrat')
    assert get_update_status(client, res.key) == \
        api.JobUpdateStatus.ROLL_FORWARD_AWAITING_PULSE

    client.pulse_job_update(res.key)
    wait_for_update_status(
        client,
        res.key,
        {
            api.JobUpdateStatus.ROLL_FORWARD_AWAITING_PULSE,
            api.JobUpdateStatus.ROLLING_FORWARD,
        },
        api.JobUpdateStatus.ROLLED_FORWARD)


def test__start_job_update_revocable_job(client):
    """
    Given 12 non-revocable cpus, and 12 revocable cpus
    Create a non-revocable of 10 instance, with 1 CPU per instance
    Create a revocable job of 3 instance, with 1 CPU per instance
    """
    non_revocable_job = start_job_update(
        client,
        'test_dc_labrat_large.yaml',
        'start job update test/dc/labrat_large')

    # validate 10 non-revocable tasks are running
    res = client.get_tasks_without_configs(api.TaskQuery(
        jobKeys={non_revocable_job},
        statuses={api.ScheduleStatus.RUNNING}
    ))
    assert len(res.tasks) == 10

    revocable_job = start_job_update(
        client,
        'test_dc_labrat_revocable.yaml',
        'start job update test/dc/labrat_revocable')

    # validate 3 revocable tasks are running
    res = client.get_tasks_without_configs(api.TaskQuery(
        jobKeys={revocable_job},
        statuses={api.ScheduleStatus.RUNNING}
    ))
    assert len(res.tasks) == 3
