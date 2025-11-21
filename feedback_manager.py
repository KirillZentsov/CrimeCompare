from supabase_utils import get_supabase_client, supabase_query


def save_feedback(user_id: str, rating: int, text: str, page: str) -> None:
    """
    Save user feedback to the database.
    
    Args:
        user_id: User identifier
        rating: Rating from 1-5
        text: Feedback text/comments
        page: Page/feature name where feedback was given
    """
    def insert():
        client = get_supabase_client()
        return client.table("feedback").insert({
            "user_id": user_id,
            "rating": rating,
            "text": text,
            "page": page
        }).execute()
    
    supabase_query(insert)
