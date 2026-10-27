"""用户要求由独立预期核对，不让被测程序自称已经理解原话。"""
from eval.cloud_upload200 import condition_errors


def mismatches(request, **expected):
    conditions=request.for_goal(request.goals[0]).conditions.model_dump(mode='json')
    return condition_errors(expected,conditions)
