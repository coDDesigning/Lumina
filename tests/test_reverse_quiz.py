import json

from backend.app.models import GeneratedOutput
from schemas.reverse_quiz import ConceptStatus

def test_reverse_quiz_adds_misconceptions_to_weak_topics(upload_api) -> None:
    # 1. Create a GeneratedOutput entry with misconceptions
    content = {
        "id": 1,
        "course_id": upload_api.course_id,
        "topic": "Photosynthesis",
        "explanation": "Plants get food from soil",
        "feedback": "Plants make food via photosynthesis.",
        "misconceptions": [
            {
                "concept": "Plant nutrition",
                "status": ConceptStatus.CONTRADICTED.value,
                "detail": "Plants make food via photosynthesis"
            }
        ]
    }
    
    with upload_api.session_factory() as session:
        output = GeneratedOutput(
            course_id=upload_api.course_id,
            user_id=upload_api.user_id,
            output_type="reverse_quiz",
            content=json.dumps(content)
        )
        session.add(output)
        session.commit()

    # 2. Query the progress endpoint
    response = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/progress",
        headers=upload_api.authorization,
    )
    
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    
    # 3. Verify that the weak topics include the reverse quiz topic
    assert "Photosynthesis (Reverse Quiz)" in payload["weak_topics"]

def test_reverse_quiz_omits_mastered_topics_from_weak_topics(upload_api) -> None:
    content = {
        "id": 2,
        "course_id": upload_api.course_id,
        "topic": "Cell Biology",
        "explanation": "Cells are the basic unit of life.",
        "feedback": "Great job.",
        "misconceptions": []
    }
    
    with upload_api.session_factory() as session:
        output = GeneratedOutput(
            course_id=upload_api.course_id,
            user_id=upload_api.user_id,
            output_type="reverse_quiz",
            content=json.dumps(content)
        )
        session.add(output)
        session.commit()

    response = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/progress",
        headers=upload_api.authorization,
    )
    
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    
    assert "Cell Biology (Reverse Quiz)" not in payload["weak_topics"]
