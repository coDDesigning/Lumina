from typing import List, Optional
from schemas.user import UserCreate, UserUpdate, UserResponse, Role
from utils.exceptions import NotFoundException
from utils.security import get_password_hash

_users_db: List[dict] = []
_id_counter = 1


class UserService:
    """Handles business logic and data access for users."""

    @staticmethod
    def get_user_by_email(email: str) -> Optional[dict]:
        """Finds a user by their email address."""
        for user in _users_db:
            if user["email"] == email:
                return user
        return None

    @staticmethod
    def get_user_by_id(user_id: int) -> Optional[dict]:
        for user in _users_db:
            if user["id"] == user_id:
                return user
        return None

    @staticmethod
    def create_user(user_data: UserCreate) -> dict:
        """Registers a new user and auto-assigns ADMIN to the first user."""
        global _id_counter
        # If this is the first user in DB, make them ADMIN automatically.
        role = Role.ADMIN if len(_users_db) == 0 else Role.USER

        new_user = user_data.model_dump()
        new_user["id"] = _id_counter
        new_user["password"] = get_password_hash(user_data.password)
        new_user["role"] = role
        new_user["is_banned"] = False
        new_user["credits"] = (
            100.0 if role == Role.USER else float("inf")
        )  # Admins have unlimited credits
        new_user["preferred_model"] = "gpt-4o-mini"

        _users_db.append(new_user)
        _id_counter += 1
        return new_user

    @staticmethod
    def get_all_users() -> List[UserResponse]:
        """Returns a list of all registered users."""
        return [UserResponse(**u) for u in _users_db]

    @staticmethod
    def update_user(email: str, update_data: UserUpdate) -> UserResponse:
        """Updates specific fields of a user by email."""
        user = UserService.get_user_by_email(email)
        if not user:
            raise NotFoundException("User not found")

        update_dict = update_data.model_dump(exclude_unset=True)
        user.update(update_dict)
        return UserResponse(**user)
