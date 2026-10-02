from eval.run_v3_regressions import production_hashes


def test_regression_freeze_detects_java_and_source_verification_changes(tmp_path):
    for name in ('src/utils/original_regions.py','backend-java/src/main/Task.java','frontend/src/Chat.vue'):
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('initial',encoding='utf-8')
    config=tmp_path/'.env.integration';config.write_text('PRIVATE_VALUE=do-not-record',encoding='utf-8')
    initial=production_hashes(tmp_path)
    assert len(initial)==4 and all(len(value)==64 for value in initial.values())
    assert 'do-not-record' not in str(initial)
    (tmp_path/'backend-java/src/main/Task.java').write_text('modified',encoding='utf-8')
    assert production_hashes(tmp_path)!=initial
