// Preserve bookmarked older-book links while using the unified workspace.
const oldBook = new URL(location.href).searchParams.get('book') || 'a';
location.replace(`engine-book.html?book=${encodeURIComponent(oldBook)}`);
