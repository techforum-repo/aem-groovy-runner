// Plain-Java stand-ins for the console's bindings: AEM's real Session /
// ResourceResolver / PageManager are Java objects (POJOs), which Groovy
// dispatches differently from Groovy classes, so the guard is tested on both.
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

public class JavaStubs {
    public static final List<String> CALLS = new ArrayList<>();

    public static class Session {
        public void save() { CALLS.add("save"); }
        public String getUserID() { CALLS.add("getUserID"); return "tech"; }
    }

    public static class Resolver {
        public void commit() { CALLS.add("commit"); }
        public Object create(Object parent, String name, Map<String, Object> props) { CALLS.add("create"); return null; }
        public Object getResource(String path) { CALLS.add("getResource"); return path; }
    }

    public static class PageManager {
        public Object getPage(String path) { CALLS.add("getPage"); return path; }
        public void delete(Object page, boolean shallow) { CALLS.add("delete"); }
    }
}
